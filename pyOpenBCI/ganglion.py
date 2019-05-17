from bluepy.btle import DefaultDelegate, Peripheral, Scanner
import atexit
import sys
import warnings
import datetime
from bitstring import BitArray
import numpy as np


# TODO: Add aux data
# TODO: Reconnecting when dropped

SAMPLE_RATE = 200.0  # Hz

# service for communication, as per docs
BLE_SERVICE = "fe84"
# characteristics of interest
BLE_CHAR_RECEIVE = "2d30c082f39f4ce6923f3484ea480596"
BLE_CHAR_SEND = "2d30c083f39f4ce6923f3484ea480596"
BLE_CHAR_DISCONNECT = "2d30c084f39f4ce6923f3484ea480596"

class OpenBCIGanglion(object):
    """ OpenBCIGanglion handles the connection to an OpenBCI Ganglion board.

    The OpenBCIGanglion class interfaces with the Cyton Dongle and the Cyton board to parse the data received and output it to Python as a OpenBCISample object.

    Args:
        mac: A string representing the Ganglion board mac address. It should be a string comprising six hex bytes separated by colons, e.g. "11:22:33:ab:cd:ed". If no mac address specified, a connection will be stablished with the first Ganglion found (Will need root privilages).

        max_packets_skipped: An integer specifying how many packets can be dropped before attempting to reconnect.
    """
    def __init__(self, mac=None, max_packets_skipped=15):
        if not mac:
            self.mac_address = self.find_mac()
        else:
            self.mac_address = mac
        self.max_packets_skipped = max_packets_skipped
        self.streaming = False
        self.board_type = 'Ganglion'

        atexit.register(self.disconnect)

        self.connect()

    def write_command(self, command):
        """Sends string command to the Ganglion board."""
        self.char_write.write(str.encode(command))

    def connect(self):
        """Establishes connection with the specified Ganglion board."""
        self.ganglion = Peripheral(self.mac_address, 'random')

        self.service = self.ganglion.getServiceByUUID(BLE_SERVICE)

        self.char_read = self.service.getCharacteristics(BLE_CHAR_RECEIVE)[0]

        self.char_write = self.service.getCharacteristics(BLE_CHAR_SEND)[0]

        self.char_discon = self.service.getCharacteristics(BLE_CHAR_DISCONNECT)[0]

        self.ble_delegate = GanglionDelegate(self.max_packets_skipped)
        self.ganglion.setDelegate(self.ble_delegate)

        self.desc_notify = self.char_read.getDescriptors(forUUID=0x2902)[0]

        try:
            self.desc_notify.write(b"\x01")
        except Exception as e:
            print("Something went wrong while trying to enable notification: " + str(e))
            sys.exit(2)

        print("Connection established")

    def disconnect(self):
        """Disconnets from the Ganglion board."""
        if self.streaming:
            self.stop_stream()

        self.char_discon.write(b' ')
        self.ganglion.disconnect()

    def find_mac(self):
        """Finds and returns the mac address of the first Ganglion board found"""
        scanner = Scanner()
        devices = scanner.scan(5)

        if len(devices) < 1:
            raise OSError('No nearby Devices found. Make sure your Bluetooth Connection is on.')

        else:
            gang_macs = []
            for dev in devices:
                for adtype, desc, value in dev.getScanData():
                    if desc == 'Complete Local Name' and value.startswith('Ganglion'):
                        gang_macs.append(dev.addr)
                        print(value)

        if len(gang_macs) < 1:
            raise OSError('Cannot find OpenBCI Ganglion Mac address.')
        else:
            print("Connecting to Ganglion with mac address: "+ gang_macs[0])
            return gang_macs[0]

    def stop_stream(self):
        """Stops Ganglion Stream."""
        self.streaming = False
        self.write_command('s')

    def start_stream(self, callback):
        """Start handling streaming data from the Ganglion board. Call a provided callback for every single sample that is processed."""
        if not self.streaming:
            self.streaming = True
            self.dropped_packets = 0
            self.write_command('b')

        if not isinstance(callback, list):
            callback = [callback]

        while self.streaming:
            try:
                self.ganglion.waitForNotifications(1./SAMPLE_RATE)
            except Exception as e:
                print(e)
                print('Something went wrong')
                sys.exit(1)

            samples = self.ble_delegate.getSamples()
            if samples:
                for sample in samples:
                    for call in callback:
                        call(sample)



class GanglionDelegate(DefaultDelegate):
    """ Delegate Object used by bluepy. Parses the Ganglion Data to return an OpenBCISample object.
    """
    def __init__(self, max_packets_skipped=15):

        DefaultDelegate.__init__(self)
        self.max_packets_skipped = max_packets_skipped
        self.last_values = [0, 0, 0, 0]
        self.last_id = -1
        self.samples = []
        self.start_time = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")

    def handleNotification(self, cHandle, data):
        """Called when data is received. It parses the raw data from the Ganglion and returns an OpenBCISample object"""
        if len(data) < 1:
            warnings.warn('A packet should at least hold one byte...')
        self.parse_raw(data)

    def parse_raw(self, raw_data):
        """Parses the data from the Cyton board into an OpenBCISample object."""
        if type(raw_data) == str:
            data = struct.unpack(str(len(packet)) + 'B', "".join(packet))
        else:
            data = raw_data

        start_byte = raw_data[0]
        bit_array = BitArray()
        self.checked_dropped(start_byte)
        # print(start_byte, start_byte == 0)

        if start_byte == 0:
            for byte in raw_data[1:13]:
                bit_array.append('0b{0:08b}'.format(byte))
                results = []
                # and split it into 24-bit chunks here
                for sub_array in bit_array.cut(24):
                    # calling ".int" interprets the value as signed 2's complement
                    results.append(sub_array.int)
                    self.last_values = np.array(results)
                    # print(self.last_values)
                    self.push_sample( [np.append(start_byte, self.last_values)])

        elif start_byte >=1 and start_byte <=100:
            for byte in raw_data[1:-1]:
                bit_array.append('0b{0:08b}'.format(byte))
                deltas = []
                for sub_array in bit_array.cut(18):

                    deltas.append(self.decompress_signed(sub_array))

                    delta1 , delta2 = np.array(deltas[:4]) , np.array(deltas[4:])

                    self.last_values1 = self.last_values - delta1
                    self.last_values = self.last_values1 - delta2

                    self.push_sample([self.last_values1, self.last_values])

        elif start_byte >=101 and start_byte <=200:
                for byte in raw_data[1:]:
                    bit_array.append('0b{0:08b}'.format(byte))
                deltas = []
                for sub_array in bit_array.cut(19):
                    deltas.append(self.decompress_signed(sub_array))

                delta1 , delta2 = np.array(deltas[:4]) , np.array(deltas[4:])
                self.last_values1 = self.last_values - delta1
                # print(self.last_values1)
                self.last_values = self.last_values1 - delta2
                # print(self.last_values)
                self.push_sample([np.append(start_byte,self.last_values1), np.append(start_byte,self.last_values)])

        # self.push_sample(data)

    def push_sample(self, data):
        """Creates a stack with the last ganglion Samples"""
        for data_arr in data:
            if len(data_arr) == 5:
                sample = OpenBCISample(data_arr[0], data_arr[1:], [], self.start_time, 'Ganglion')
                self.samples.append(sample)

    def getSamples(self):
        """Returns the last OpenBCI Samples in the stack"""
        old_samples = self.samples
        self.samples = []
        return old_samples

    def checked_dropped(self, num):
        """Checks dropped packets"""
        if num not in [206, 207]:
            if self.last_id == 0 and num not in [1, 101]:
                if num > 100:
                    dropped = num - 100
                else:
                    dropped = num
            elif self.last_id == 0:
                dropped = 0
            elif self.last_id > num:
                dropped = 100 - abs(self.last_id - num)
            else:
                dropped = abs(self.last_id - num)

            if dropped > self.max_packets_skipped:
                print("Dropped %d packets...." % dropped)

            self.last_id = num

    def decompress_signed(self, bit_array):
        """Used to decrompress signed bit arrays."""
        result = bit_array.int
        if bit_array.endswith('0b1'):
            result -= 1
        return result

class OpenBCISample():
    """ Object that encapsulates a single sample from the OpenBCI board.

    Attributes:
        id: An int representing the packet id of the aquired sample.
        channels_data: An array with the data from the board channels.
        aux_data: An array with the aux data from the board.
        start_time: A string with the stream start time.
        board_type: A string specifying the board type, e.g 'cyton', 'daisy', 'ganglion'
    """
    def __init__(self, packet_id, channels_data, aux_data, init_time, board_type):
        self.id = packet_id
        self.channels_data = channels_data
        self.aux_data = aux_data
        self.start_time = init_time
        self.board_type = board_type
