import serial
from serial import Serial

from threading import Timer
import time
import logging
import sys
import struct
import numpy as np
import atexit
import datetime
import glob
# Define variables
SAMPLE_RATE = 250.0  # Hz
START_BYTE = 0xA0  # start of data packet
END_BYTE = 0xC0  # end of data packet



class OpenBCICyton(object):
    """ OpenBCICyton handles the connection to an OpenBCI Cyton board.

    The OpenBCICyton class interfaces with the Cyton Dongle and the Cyton board to parse the data received and output it to Python as a OpenBCISample object.

    Args:
        port: A string representing the COM port that the Cyton Dongle is connected to. e.g for Windows users 'COM3', for MacOS or Linux users '/dev/ttyUSB1'. If no port is specified it will try to find the first port available.

        daisy: A boolean indicating if there is a Daisy connected to the Cyton board.

        baud: An integer specifying the baudrate of the serial connection. The maximum baudrate of the Cyton board is 115200.

        timeout: An float specifying the maximum milliseconds to wait for serial data.

        max_packets_skipped: An integer specifying how many packets can be dropped before attempting to reconnect.

    """
    def __init__(self, port=None, daisy=False, baud=115200, timeout=None, max_packets_skipped=1):
        self._logger = logging.getLogger(self.__class__.__name__)

        self.baud = baud
        self.timeout = timeout
        self.daisy = daisy
        self.max_packets_skipped = max_packets_skipped
        self.streaming = False
        if port:
            self.port = port
        else:
            self.port = self.find_port()
        self.start_time = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        if self.daisy:
            self.board_type = "CytonDaisy"
        else:
            self.board_type = "Cyton"

        # Connecting to the board
        self.ser = Serial(port=self.port, baudrate=self.baud, timeout=self.timeout)

        self._logger.info("Serial established.")

        # Perform a soft reset of the board
        time.sleep(2)
        self.ser.write(b'v')


        # wait for device to be ready
        time.sleep(1)

        if port != "loop://":
            self.print_incoming_text()


        self.packets_dropped = 0
        self.read_state = 0
        self.last_odd_sample = OpenBCISample(-1, [], [], self.start_time, self.board_type)  # used for daisy


        # Disconnects from board when terminated
        atexit.register(self.disconnect)

    def disconnect(self):
        """Disconnects the OpenBCI Serial."""
        if self.ser.isOpen():
            self._logger.info("Closing serial.")
            self.ser.close()

    def find_port(self):
        """Finds the port to which the Cyton Dongle is connected to."""
        # Find serial port names per OS
        if sys.platform.startswith('win'):
            ports = ['COM%s' % (i + 1) for i in range(256)]
        elif sys.platform.startswith('linux') or sys.platform.startswith('cygwin'):
            ports = glob.glob('/dev/ttyUSB*')
        elif sys.platform.startswith('darwin'):
            ports = glob.glob('/dev/tty.usbserial*')
        else:
            raise EnvironmentError('Error finding ports on your operating system')

        openbci_port = ''
        for port in ports:
            try:
                s = Serial(port=port, baudrate=self.baud, timeout=self.timeout)
                s.write(b'v')
                line = ''
                time.sleep(2)
                if s.inWaiting():
                    line = ''
                    c = ''
                    while '$$$' not in line:
                        c = s.read().decode('utf-8', errors='replace')
                        line += c
                    if 'OpenBCI' in line:
                        openbci_port = port
                s.close()
            except (OSError, serial.SerialException):
                pass
        if openbci_port == '':
            raise OSError('Cannot find OpenBCI port.')
        else:
            return openbci_port

    def stop_stream(self):
        """Stops Stream from the Cyton board."""
        self.streaming = False
        self.ser.write(b's')

    def reconnect(self):
        """Attempts to reconnect to the Cyton board if the connection was lost."""
        self.packets_dropped = 0
        self._logger.info("Reconnecting...")

        # Stop stream
        self.stop_stream()
        time.sleep(0.5)

        # Soft reset of the board
        self.ser.write(b'v')
        time.sleep(0.5)

        # Start stream
        self.ser.write(b'b')
        time.sleep(0.5)
        self.streaming = True

    def check_connection(self, max_packets_skipped=1, interval=2):
        """Verifies if the connection is stable. If not, it attempts to reconnect to the board"""
        if not self.streaming:
            self._logger.warning("Not streaming!")
            return

        # check number of dropped packets and reconnect if problem is too large
        elif self.packets_dropped > max_packets_skipped:
                #if error attempt to reconnect
                self.reconnect()

        # Check connection every 'interval' seconds
        Timer(interval, self.check_connection).start()


    def parse_board_data(self, maxbytes2skip=3000):
        """Parses the data from the Cyton board into an OpenBCISample object."""
        def read_board(n):
            bb = self.ser.read(n)
            if not bb:
                self._logger.warning("Device appears to be stalling. "
                                     "Quitting...")
                sys.exit()
                raise Exception('Device Stalled')
                sys.exit()
                return '\xFF'
            else:
                return bb

        for rep in range(maxbytes2skip):

            # Start Byte & ID
            if self.read_state == 0:
                b = read_board(1)

                if struct.unpack('B', b)[0] == START_BYTE:
                    if rep != 0:
                        self._logger.info(
                            "Skipped %d bytes before start found" % rep)
                        rep = 0

                    packet_id = struct.unpack('B', read_board(1))[0]
                    log_bytes_in = str(packet_id)

                    self.read_state = 1

            # Channel data
            elif self.read_state == 1:
                channels_data = []
                for c in range(8):
                    # Read 3 byte integers
                    literal_read = read_board(3)

                    unpacked = struct.unpack('3B', literal_read)
                    log_bytes_in = log_bytes_in + '|' + str(literal_read)

                    # Translate 3 byte int into 2s complement
                    if unpacked[0] > 127:
                        pre_fix = bytes(bytearray.fromhex('FF'))
                    else:
                        pre_fix = bytes(bytearray.fromhex('00'))

                    literal_read = pre_fix + literal_read

                    myInt = struct.unpack('>i', literal_read)[0]

                    # Append channel to channels data
                    channels_data.append(myInt)

                self.read_state = 2

            # Read Aux Data
            elif self.read_state == 2:
                aux_data = []
                for a in range(3):

                    acc = struct.unpack('>h', read_board(2))[0]
                    log_bytes_in = log_bytes_in + '|' + str(acc)

                    # Append to auxiliary data array
                    aux_data.append(acc)

                self.read_state = 3

            # Read End Byte
            elif self.read_state == 3:
                val = struct.unpack('B', read_board(1))[0]

                log_bytes_in = log_bytes_in + '|' + str(val)
                self.read_state = 0 # resets to read next packet

                if val == END_BYTE:
                    sample = OpenBCISample(packet_id, channels_data, aux_data, self.start_time, self.board_type)
                    self.packets_dropped = 0
                    return sample
                else:
                    self._logger.warning("ID:<%d> <Unexpected END_BYTE found <%s> instead of <%s>" % (packet_id, val, END_BYTE))
                    self.packets_dropped = self.packets_dropped + 1


    def write_command(self, command):
        """Sends string command to the Cyton board"""
        if command == '?':
            self.ser.write(command.encode())
            if self.ser.inWaiting():
                line = ''
                while '$$$' not in line:
                    line += self.ser.read().decode('utf-8', errors='replace')
                print(line)
        else:
            self.ser.write(command.encode())
            time.sleep(0.5)


    def start_stream(self, callback):
        """Start handling streaming data from the board. Call a provided callback for every single sample that is processed."""
        if not self.streaming:
            self.ser.write(b'b')
            self.streaming = True

        # Enclose callback function in a list
        if not isinstance(callback, list):
            callback = [callback]

        # checks connection
        self.check_connection(max_packets_skipped=self.max_packets_skipped)

        while self.streaming:

            #read current sample
            sample = self.parse_board_data()

            if not self.daisy:
                 for call in callback:
                     call(sample)

            # When daisy is connected wait to concatenate two samples
            else:
                # odd sample is daisy sample use later
                if ~sample.id % 2:
                    self.last_odd_sample = sample

                # Check if the next sample ID is concecutive, if not the packet is dropped
                elif sample.id - 1 == self.last_odd_sample.id:
                    # The auxiliary data is the average between the two samples.
                    avg_aux_data = list((np.array(sample.aux_data) + np.array(self.last_odd_sample.aux_data)) / 2)

                    sample_with_daisy = OpenBCISample(sample.id, sample.channels_data + self.last_odd_sample.channels_data, avg_aux_data, self.start_time, self.board_type)

                    for call in callback:
                        call(sample_with_daisy)
                        
    def print_incoming_text(self):
        """
        When starting the connection, print all the debug data until
        we get to a line with the end sequence '$$$'.
        """
        line = ''
        # Wait for device to send data
        time.sleep(1)

        if self.ser.inWaiting():
            line = ''
            c = ''
            # Look for end sequence $$$
            while '$$$' not in line:
                # we're supposed to get UTF8 text, but the board might behave otherwise
                c = self.ser.read().decode('utf-8',
                                           errors='replace')
                line += c
            self._logger.debug(line)
        else:
            self.warn("No Message")


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
