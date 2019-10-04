import atexit
import datetime
import logging
import sys
import threading
import warnings

import numpy as np
from bitstring import BitArray
from bluepy.btle import DefaultDelegate, Peripheral, Scanner

# TODO: Add aux data
# TODO: Reconnecting when dropped

SAMPLE_RATE = 200.0  # Hz
DELTA_T = 1.0 / SAMPLE_RATE

# service for communication, as per docs
BLE_SERVICE = "fe84"
# characteristics of interest
BLE_CHAR_RECEIVE = "2d30c082f39f4ce6923f3484ea480596"
BLE_CHAR_SEND = "2d30c083f39f4ce6923f3484ea480596"
BLE_CHAR_DISCONNECT = "2d30c084f39f4ce6923f3484ea480596"


def _find_mac():
    """Finds and returns the mac address of the first Ganglion board
    found"""
    scanner = Scanner()
    devices = scanner.scan(5)

    if len(devices) < 1:
        raise OSError(
            'No nearby Devices found. Make sure your Bluetooth Connection '
            'is on.')

    else:
        gang_macs = []
        for dev in devices:
            for adtype, desc, value in dev.getScanData():
                if desc == 'Complete Local Name' and value.startswith(
                        'Ganglion'):
                    gang_macs.append(dev.addr)
                    print(value)

    if len(gang_macs) < 1:
        raise OSError('Cannot find OpenBCI Ganglion Mac address.')
    else:
        return gang_macs[0]


class OpenBCIGanglion(object):
    """ OpenBCIGanglion handles the connection to an OpenBCI Ganglion board.

    The OpenBCIGanglion class interfaces with the Cyton Dongle and the Cyton
    board to parse the data received and output it to Python as a
    OpenBCISample object.

    Args:
        mac: A string representing the Ganglion board mac address. It should
        be a string comprising six hex bytes separated by colons,
        e.g. "11:22:33:ab:cd:ed". If no mac address specified, a connection
        will be stablished with the first Ganglion found (Will need root
        privilages).

        max_packets_skipped: An integer specifying how many packets can be
        dropped before attempting to reconnect.
    """

    def __init__(self, mac=None, max_packets_skipped=15):
        self._logger = logging.getLogger(self.__class__.__name__)

        if not mac:
            self.mac_address = _find_mac()
        else:
            self.mac_address = mac
        self._logger.debug(
            'Connecting to Ganglion with MAC address %s' % mac)

        self.max_packets_skipped = max_packets_skipped
        self._stop_streaming = threading.Event()
        self._stop_streaming.set()
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

        self.char_discon = \
            self.service.getCharacteristics(BLE_CHAR_DISCONNECT)[0]

        self.ble_delegate = GanglionDelegate(self.max_packets_skipped)
        self.ganglion.setDelegate(self.ble_delegate)

        self.desc_notify = self.char_read.getDescriptors(forUUID=0x2902)[0]

        try:
            self.desc_notify.write(b"\x01")
        except Exception as e:
            self._logger.error(
                "Something went wrong while trying to enable notifications:", e)
            sys.exit(2)

        self._logger.debug("Connection established.")

    def disconnect(self):
        """Disconnets from the Ganglion board."""
        if not self._stop_streaming.is_set():
            self.stop_stream()

        try:
            self.char_discon.write(b' ')
        except Exception as e:
            # exceptions here don't really matter as we're disconnecting anyway
            # although, it would be good to check WHY self.char_discon.write()
            # ALWAYS throws an exception...
            self._logger.debug(e)
            pass

        try:
            self.ganglion.disconnect()
        except Exception as e:
            self._logger.debug(e)
            pass

    def stop_stream(self):
        """Stops Ganglion Stream."""
        self._stop_streaming.set()
        self.write_command('s')

    def start_stream(self, callback, accel_data_on=False):
        """Start handling streaming data from the Ganglion board. Call a
        provided callback for every single sample that is processed."""

        # toggle accelerometer
        self.write_command('n' if accel_data_on else 'N')

        if self._stop_streaming.is_set():
            self._stop_streaming.clear()
            self.dropped_packets = 0
            self.write_command('b')

        if not isinstance(callback, list):
            callback = [callback]

        while not self._stop_streaming.is_set():
            try:
                self.ganglion.waitForNotifications(DELTA_T)
            except Exception as e:
                self._logger.error("Something went wrong: ", e)
                sys.exit(1)

            samples = self.ble_delegate.getSamples()
            if samples:
                for sample in samples:
                    for call in callback:
                        call(sample)


class GanglionDelegate(DefaultDelegate):
    """ Delegate Object used by bluepy. Parses the Ganglion Data to return an
    OpenBCISample object.
    """

    __boardname = 'Ganglion'

    def __init__(self, max_packets_skipped=15):

        DefaultDelegate.__init__(self)
        self.max_packets_skipped = max_packets_skipped
        self.last_values = [0, 0, 0, 0]
        self.last_id = -1
        self.samples = []
        self.start_time = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")

        self._logger = logging.getLogger(self.__class__.__name__)
        self._wait_for_full_pkt = True

    def handleNotification(self, cHandle, data):
        """Called when data is received. It parses the raw data from the
        Ganglion and returns an OpenBCISample object"""

        if len(data) < 1:
            warnings.warn('A packet should at least hold one byte...')
        self.parse_raw(data)

    def parse_raw(self, raw_data):
        """Parses the data from the Cyton board into an OpenBCISample object."""
        if type(raw_data) == str:
            data = struct.unpack(str(len(packet)) + 'B', "".join(packet))
        else:
            data = raw_data

        bit_array = BitArray()

        start_byte = raw_data[0]
        dropped, dummy_samples = self.check_dropped(start_byte)
        self.last_id = start_byte

        if self._wait_for_full_pkt:
            if start_byte != 0:
                self._logger.warning('Need to wait for next full packet...')
                if dropped > 0:
                    self.samples.extend(dummy_samples)
                else:
                    self.samples.extend([
                        OpenBCISample(start_byte, [np.NaN] * 4, [],
                                      self.start_time, self.__boardname),
                        OpenBCISample(start_byte, [np.NaN] * 4, [],
                                      self.start_time, self.__boardname)

                    ])
                return
            else:
                self._logger.warning('Got full packet, resuming.')
                self._wait_for_full_pkt = False

        if dropped > 0:
            self._logger.error('Dropped %d packets! '
                               'Need to wait for next full packet...' % dropped)

            self.samples.extend(dummy_samples)
            self._wait_for_full_pkt = True
            return

        if start_byte == 0:
            # uncompressed sample
            for byte in raw_data[1:13]:
                bit_array.append('0b{0:08b}'.format(byte))

            results = []
            # and split it into 24-bit chunks here
            for sub_array in bit_array.cut(24):
                # calling ".int" interprets the value as signed 2's complement
                results.append(sub_array.int)

            self.last_values = np.array(results, dtype=np.int32)

            # store the sample
            self.samples.append(
                OpenBCISample(start_byte, self.last_values, [],
                              self.start_time, self.__boardname))

        elif 1 <= start_byte <= 200:
            for byte in raw_data[1:]:
                bit_array.append('0b{0:08b}'.format(byte))

            deltas = []
            if start_byte <= 100:
                # 18-bit compressed sample
                for sub_array in bit_array.cut(18):
                    deltas.append(self.decompress_signed(sub_array))
            else:
                # 19-bit compressed sample
                for sub_array in bit_array.cut(19):
                    deltas.append(self.decompress_signed(sub_array))

            delta1 = np.array(deltas[:4], dtype=np.int32)
            delta2 = np.array(deltas[4:], dtype=np.int32)

            self.last_values1 = self.last_values - delta1
            self.last_values = self.last_values1 - delta2

            # since compressed packets include two samples which have been
            # processed client-side, prefer to calculate timestamp as
            # expected timestamp with respect to the most-recent full-size
            # packet received

            # store both samples
            self.samples.append(
                OpenBCISample(start_byte, self.last_values1, [],
                              self.start_time, self.__boardname))

            self.samples.append(
                OpenBCISample(start_byte, self.last_values, [],
                              self.start_time, self.__boardname))

    def getSamples(self):
        """Returns the last OpenBCI Samples in the stack"""
        old_samples = self.samples
        self.samples = []
        return old_samples

    def check_dropped(self, num):
        """Checks dropped packets"""
        dropped = 0
        dummy_samples = []
        if num not in [0, 206, 207]:
            if self.last_id == 0:
                if num >= 101:
                    dropped = num - 101
                else:
                    dropped = num - 1
            else:
                dropped = (num - self.last_id) - 1

            # generate dummy samples
            # generate NaN samples for the callback
            dummy_samples = []
            for i in range(dropped, -1, -1):
                dummy_samples.extend([
                    OpenBCISample(num - i, [np.NaN] * 4, [],
                                  self.start_time, self.__boardname),
                    OpenBCISample(num - i, [np.NaN] * 4, [],
                                  self.start_time, self.__boardname)

                ])
        return dropped, dummy_samples

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
        board_type: A string specifying the board type, e.g 'cyton', 'daisy',
        'ganglion'
    """

    def __init__(self, packet_id, channels_data, aux_data,
                 init_time, board_type):
        self.id = packet_id
        self.channels_data = channels_data
        self.aux_data = aux_data
        self.start_time = init_time
        self.board_type = board_type
