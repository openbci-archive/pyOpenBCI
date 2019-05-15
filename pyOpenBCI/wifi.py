"""
Core OpenBCI object for handling connections and samples from the WiFi Shield
Note that the LIB will take care on its own to print incoming ASCII messages if any (FIXME, BTW).
EXAMPLE USE:
def handle_sample(sample):
  print(sample.channels_data)
wifi = OpenBCIWifi()
wifi.start(handle_sample)

TODO: Cyton/Ganglion JSON
TODO: Ganglion Raw
TODO: Cyton Raw
"""
from __future__ import print_function
import asyncore
import atexit
import json
import logging
import re
import socket
import timeit
import time
import struct

try:
    import urllib2
except ImportError:
    import urllib

import requests
import xmltodict

from pyOpenBCI.utils import ssdp

SAMPLE_RATE = 0  # Hz

'''
#Commands for in SDK
command_stop = "s";
command_startBinary = "b";
'''


class OpenBCIWiFi(object):
    """
    Handle a connection to an OpenBCI wifi shield.
    Args:
      ip_address: The IP address of the WiFi Shield, "None" to attempt auto-detect.
      shield_name: The unique name of the WiFi Shield, such as `OpenBCI-2AD4`, will use SSDP to
        get IP address still, if `shield_name` is "None" and `ip_address` is "None",
        will connect to the first WiFi Shield found using SSDP
      sample_rate: The sample rate to set the attached board to. If the sample rate picked
        is not a sample rate the attached board can support, i.e. you send 300 to Cyton,
        then error will be thrown.
      log:
      timeout: in seconds, disconnect / reconnect after a period without new data
        should be high if impedance check
      max_packets_to_skip: will try to disconnect / reconnect after too many packets are skipped
    """

    def __init__(self, ip_address=None, shield_name=None, sample_rate=None, log=True, timeout=3,
                 max_packets_to_skip=20, latency=10000, high_speed=True, ssdp_attempts=5,
                 num_channels=8, local_ip_address=None):
        # these one are used
        self.daisy = False
        self.gains = None
        self.high_speed = high_speed
        self.impedance = False
        self.ip_address = ip_address
        self.latency = latency
        self.log = log  # print_incoming_text needs log
        self.max_packets_to_skip = max_packets_to_skip
        self.num_channels = num_channels
        self.sample_rate = sample_rate
        self.shield_name = shield_name
        self.ssdp_attempts = ssdp_attempts
        self.streaming = False
        self.timeout = timeout

        # might be handy to know API
        self.board_type = "none"
        # number of EEG channels
        self.eeg_channels_per_sample = 0
        self.read_state = 0
        self.log_packet_count = 0
        self.packets_dropped = 0
        self.time_last_packet = 0

        if self.log:
            print("Welcome to OpenBCI Native WiFi Shield Driver - Please contribute code!")

        self.local_ip_address = local_ip_address
        if not self.local_ip_address:
            self.local_ip_address = self._get_local_ip_address()

        # Intentionally bind to port 0
        self.local_wifi_server = WiFiShieldServer(self.local_ip_address, 0)
        self.local_wifi_server_port = self.local_wifi_server.socket.getsockname()[1]
        if self.log:
            print("Opened socket on %s:%d" %
                  (self.local_ip_address, self.local_wifi_server_port))

        if ip_address is None:
            for i in range(ssdp_attempts):
                try:
                    self.find_wifi_shield(wifi_shield_cb=self.on_shield_found)
                    break
                except OSError:
                    # Try again
                    if self.log:
                        print("Did not find any WiFi Shields")
        else:
            self.on_shield_found(ip_address)

    def on_shield_found(self, ip_address):
        self.ip_address = ip_address
        self.connect()
        # Disconnects from board when terminated
        atexit.register(self.disconnect)

    def loop(self):
        asyncore.loop()

    def _get_local_ip_address(self):
        """
        Gets the local ip address of this computer
        @returns str Local IP address
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip_address = s.getsockname()[0]
        s.close()
        return local_ip_address

    def getBoardType(self):
        """ Returns the version of the board """
        return self.board_type

    def setImpedance(self, flag):
        """ Enable/disable impedance measure """
        self.impedance = bool(flag)

    def connect(self):
        """ Connect to the board and configure it. Note: recreates various objects upon call. """
        if self.ip_address is None:
            raise ValueError('self.ip_address cannot be None')

        if self.log:
            print("Init WiFi connection with IP: " + self.ip_address)

        """
        Docs on these HTTP requests and more are found:
        https://app.swaggerhub.com/apis/pushtheworld/openbci-wifi-server/1.3.0
        """

        res_board = requests.get("http://%s/board" % self.ip_address)

        if res_board.status_code == 200:
            board_info = res_board.json()
            if not board_info['board_connected']:
                raise RuntimeError("No board connected to WiFi Shield. "
                                   "To learn how to connect to a Cyton or Ganglion visit "
                                   "http://docs.openbci.com/Tutorials/03-Wifi_Getting_Started_Guide")
            self.board_type = board_info['board_type']
            self.eeg_channels_per_sample = board_info['num_channels']
            if self.log:
                print("Connected to %s with %s channels" %
                      (self.board_type, self.eeg_channels_per_sample))

        self.gains = None
        if self.board_type == 'cyton':
            self.gains = [24, 24, 24, 24, 24, 24, 24, 24]
            self.daisy = False
        elif self.board_type == 'daisy':
            self.gains = [24, 24, 24, 24, 24, 24, 24,
                          24, 24, 24, 24, 24, 24, 24, 24, 24]
            self.daisy = True
        elif self.board_type == 'ganglion':
            self.gains = [51, 51, 51, 51]
            self.daisy = False
        self.local_wifi_server.set_daisy(daisy=self.daisy)
        self.local_wifi_server.set_parser(
            ParseRaw(gains=self.gains, board_type=self.board_type))

        if self.high_speed:
            output_style = 'raw'
        else:
            output_style = 'json'
        res_tcp_post = requests.post("http://%s/tcp" % self.ip_address,
                                     json={
                                         'ip': self.local_ip_address,
                                         'port': self.local_wifi_server_port,
                                         'output': output_style,
                                         'delimiter': True,
                                         'latency': self.latency
                                     })
        if res_tcp_post.status_code == 200:
            tcp_status = res_tcp_post.json()
            if tcp_status['connected']:
                if self.log:
                    print("WiFi Shield to Python TCP Socket Established")
            else:
                raise RuntimeWarning("WiFi Shield is not able to connect to local server."
                                     "Please open an issue.")

    def init_streaming(self):
        """ Tell the board to record like crazy. """
        res_stream_start = requests.get(
            "http://%s/stream/start" % self.ip_address)
        if res_stream_start.status_code == 200:
            self.streaming = True
            self.packets_dropped = 0
            self.time_last_packet = timeit.default_timer()
        else:
            raise EnvironmentError("Unable to start streaming."
                                   "Check API for status code %d on /stream/start"
                                   % res_stream_start.status_code)

    def find_wifi_shield(self, shield_name=None, wifi_shield_cb=None):
        """Detects Ganglion board MAC address if more than 1, will select first. Needs root."""

        if self.log:
            print("Try to find WiFi shields on your local wireless network")
            print("Scanning for %d seconds nearby devices..." % self.timeout)

        list_ip = []
        list_id = []
        found_shield = False

        def wifi_shield_found(response):
            res = requests.get(response.location, verify=False).text
            device_description = xmltodict.parse(res)
            cur_shield_name = str(
                device_description['root']['device']['serialNumber'])
            cur_base_url = str(device_description['root']['URLBase'])
            cur_ip_address = re.findall(r'[0-9]+(?:\.[0-9]+){3}', cur_base_url)[0]
            list_id.append(cur_shield_name)
            list_ip.append(cur_ip_address)
            found_shield = True
            if shield_name is None:
                print("Found WiFi Shield %s with IP Address %s" %
                      (cur_shield_name, cur_ip_address))
                if wifi_shield_cb is not None:
                    wifi_shield_cb(cur_ip_address)
            else:
                if shield_name == cur_shield_name:
                    if wifi_shield_cb is not None:
                        wifi_shield_cb(cur_ip_address)

        ssdp_hits = ssdp.discover("urn:schemas-upnp-org:device:Basic:1", timeout=self.timeout,
                                  wifi_found_cb=wifi_shield_found)

        nb_wifi_shields = len(list_id)

        if nb_wifi_shields < 1:
            print("No WiFi Shields found ;(")
            raise OSError('Cannot find OpenBCI WiFi Shield with local name')

        if nb_wifi_shields > 1:
            print(
                "Found " + str(nb_wifi_shields) +
                ", selecting first named: " + list_id[0] +
                " with IPV4: " + list_ip[0])
            return list_ip[0]

    def write_command(self, output):
        """
        Pass through commands from the WiFi Shield to the Carrier board
        :param output:
        :return:
        """
        res_command_post = requests.post("http://%s/command" % self.ip_address,
                                         json={'command': output})
        if res_command_post.status_code == 200:
            ret_val = res_command_post.text
            if self.log:
                print(ret_val)
            return ret_val
        else:
            if self.log:
                print("Error code: %d %s" %
                      (res_command_post.status_code, res_command_post.text))
            raise RuntimeError("Error code: %d %s" % (
                res_command_post.status_code, res_command_post.text))

    def getSampleRate(self):
        return self.sample_rate

    def getNbEEGChannels(self):
        """Will not get new data on impedance check."""
        return self.eeg_channels_per_sample

    def start_stream(self, callback, lapse=-1):
        """
        Start handling streaming data from the board. Call a provided callback
        for every single sample that is processed
        Args:
          callback: A callback function, or a list of functions, that will receive a single
            argument of the OpenBCISample object captured.
        """
        start_time = timeit.default_timer()

        # Enclose callback function in a list if it comes alone
        if not isinstance(callback, list):
            self.local_wifi_server.set_callback(callback)
        else:
            self.local_wifi_server.set_callback(callback[0])

        if not self.streaming:
            self.init_streaming()

        # while self.streaming:
        #     # should the board get disconnected and we could not wait for notification anymore
        #     #  a reco should be attempted through timeout mechanism
        #     try:
        #         # at most we will get one sample per packet
        #         self.waitForNotifications(1. / self.getSampleRate())
        #     except Exception as e:
        #         print("Something went wrong while waiting for a new sample: " + str(e))
        #     # retrieve current samples on the stack
        #     samples = self.delegate.getSamples()
        #     self.packets_dropped = self.delegate.getMaxPacketsDropped()
        #     if samples:
        #         self.time_last_packet = timeit.default_timer()
        #         for call in callback:
        #             for sample in samples:
        #                 call(sample)
        #
        #     if (lapse > 0 and timeit.default_timer() - start_time > lapse):
        #         self.stop();
        #     if self.log:
        #         self.log_packet_count = self.log_packet_count + 1;
        #
        #     # Checking connection -- timeout and packets dropped
        #     self.check_connection()

    def test_signal(self, signal):
        """ Enable / disable test signal """
        if signal == 0:
            self.warn("Disabling synthetic square wave")
            try:
                self.write_command(']')
            except Exception as e:
                print("Something went wrong while setting signal: " + str(e))
        elif signal == 1:
            self.warn("Enabling synthetic square wave")
            try:
                self.write_command('[')
            except Exception as e:
                print("Something went wrong while setting signal: " + str(e))
        else:
            self.warn("%s is not a known test signal. Valid signal is 0-1" % signal)

    def set_channel(self, channel, toggle_position):
        """ Enable / disable channels """
        try:
            if channel > self.num_channels:
                raise ValueError('Cannot set non-existant channel')
            # Commands to set toggle to on position
            if toggle_position == 1:
                if channel is 1:
                    self.write_command('!')
                if channel is 2:
                    self.write_command('@')
                if channel is 3:
                    self.write_command('#')
                if channel is 4:
                    self.write_command('$')
                if channel is 5:
                    self.write_command('%')
                if channel is 6:
                    self.write_command('^')
                if channel is 7:
                    self.write_command('&')
                if channel is 8:
                    self.write_command('*')
                if channel is 9:
                    self.write_command('Q')
                if channel is 10:
                    self.write_command('W')
                if channel is 11:
                    self.write_command('E')
                if channel is 12:
                    self.write_command('R')
                if channel is 13:
                    self.write_command('T')
                if channel is 14:
                    self.write_command('Y')
                if channel is 15:
                    self.write_command('U')
                if channel is 16:
                    self.write_command('I')
            # Commands to set toggle to off position
            elif toggle_position == 0:
                if channel is 1:
                    self.write_command('1')
                if channel is 2:
                    self.write_command('2')
                if channel is 3:
                    self.write_command('3')
                if channel is 4:
                    self.write_command('4')
                if channel is 5:
                    self.write_command('5')
                if channel is 6:
                    self.write_command('6')
                if channel is 7:
                    self.write_command('7')
                if channel is 8:
                    self.write_command('8')
                if channel is 9:
                    self.write_command('q')
                if channel is 10:
                    self.write_command('w')
                if channel is 11:
                    self.write_command('e')
                if channel is 12:
                    self.write_command('r')
                if channel is 13:
                    self.write_command('t')
                if channel is 14:
                    self.write_command('y')
                if channel is 15:
                    self.write_command('u')
                if channel is 16:
                    self.write_command('i')
        except Exception as e:
            print("Something went wrong while setting channels: " + str(e))

    # See Cyton SDK for options
    def set_channel_settings(self, channel, enabled=True, gain=24, input_type=0,
                             include_bias=True, use_srb2=True, use_srb1=True):
        try:
            if channel > self.num_channels:
                raise ValueError('Cannot set non-existant channel')
            if self.board_type == 'ganglion':
                raise ValueError('Cannot use with Ganglion')
            ch_array = list("12345678QWERTYUI")
            # defaults
            command = list("x1060110X")
            # Set channel
            command[1] = ch_array[channel - 1]
            # Set power down if needed (default channel enabled)
            if not enabled:
                command[2] = '1'
            # Set gain (default 24)
            if gain == 1:
                command[3] = '0'
            if gain == 2:
                command[3] = '1'
            if gain == 4:
                command[3] = '2'
            if gain == 6:
                command[3] = '3'
            if gain == 8:
                command[3] = '4'
            if gain == 12:
                command[3] = '5'

            # TODO: Implement input type (default normal)

            # Set bias inclusion (default include)
            if not include_bias:
                command[5] = '0'
            # Set srb2 use (default use)
            if not use_srb2:
                command[6] = '0'
            # Set srb1 use (default don't use)
            if use_srb1:
                command[6] = '1'
            command_send = ''.join(command)
            self.write_command(command_send)

            # Make sure to update gain in wifi
            self.gains[channel - 1] = gain
            self.local_wifi_server.set_gains(gains=self.gains)
            self.local_wifi_server.set_parser(
                ParseRaw(gains=self.gains, board_type=self.board_type))

        except ValueError as e:
            print("Something went wrong while setting channel settings: " + str(e))

    def set_sample_rate(self, sample_rate):
        """ Change sample rate """
        try:
            if self.board_type == 'cyton' or self.board_type == 'daisy':
                if sample_rate == 250:
                    self.write_command('~6')
                elif sample_rate == 500:
                    self.write_command('~5')
                elif sample_rate == 1000:
                    self.write_command('~4')
                elif sample_rate == 2000:
                    self.write_command('~3')
                elif sample_rate == 4000:
                    self.write_command('~2')
                elif sample_rate == 8000:
                    self.write_command('~1')
                elif sample_rate == 16000:
                    self.write_command('~0')
                else:
                    print("Sample rate not supported: " + str(sample_rate))
            elif self.board_type == 'ganglion':
                if sample_rate == 200:
                    self.write_command('~7')
                elif sample_rate == 400:
                    self.write_command('~6')
                elif sample_rate == 800:
                    self.write_command('~5')
                elif sample_rate == 1600:
                    self.write_command('~4')
                elif sample_rate == 3200:
                    self.write_command('~3')
                elif sample_rate == 6400:
                    self.write_command('~2')
                elif sample_rate == 12800:
                    self.write_command('~1')
                elif sample_rate == 25600:
                    self.write_command('~0')
                else:
                    print("Sample rate not supported: " + str(sample_rate))
            else:
                print("Board type not supported for setting sample rate")
        except Exception as e:
            print("Something went wrong while setting sample rate: " + str(e))

    def set_accelerometer(self, toggle_position):
        """ Enable / disable accelerometer """
        try:
            if self.board_type == 'ganglion':
                # Commands to set toggle to on position
                if toggle_position == 1:
                    self.write_command('n')
                # Commands to set toggle to off position
                elif toggle_position == 0:
                    self.write_command('N')
            else:
                print("Board type not supported for setting accelerometer")
        except Exception as e:
            print("Something went wrong while setting accelerometer: " + str(e))

    """
    Clean Up (atexit)
    """

    def stop(self):
        print("Stopping streaming...")
        self.streaming = False
        # connection might be already down here
        try:
            if self.impedance:
                print("Stopping with impedance testing")
                self.write_command('Z')
            else:
                self.write_command('s')
        except Exception as e:
            print("Something went wrong while asking the board to stop streaming: " + str(e))
        if self.log:
            logging.warning('sent <s>: stopped streaming')

    def disconnect(self):
        if self.streaming:
            self.stop()

        # should not try to read/write anything after that, will crash

    """
        SETTINGS AND HELPERS
    """

    def warn(self, text):
        if self.log:
            # log how many packets where sent succesfully in between warnings
            if self.log_packet_count:
                logging.info('Data packets received:' +
                             str(self.log_packet_count))
                self.log_packet_count = 0
            logging.warning(text)
        print("Warning: %s" % text)

    def check_connection(self):
        """ Check connection quality in term of lag and number of packets drop.
        Reinit connection if necessary.
        FIXME: parameters given to the board will be lost.
        """
        # stop checking when we're no longer streaming
        if not self.streaming:
            return
        # check number of dropped packets and duration without new packets, deco/reco if too large
        if self.packets_dropped > self.max_packets_to_skip:
            self.warn("Too many packets dropped, attempt to reconnect")
            self.reconnect()
        elif self.timeout > 0 and timeit.default_timer() - self.time_last_packet > self.timeout:
            self.warn("Too long since got new data, attempt to reconnect")
            # if error, attempt to reconect
            self.reconnect()

    def reconnect(self):
        """ In case of poor connection, will shut down and relaunch everything.
        FIXME: parameters given to the board will be lost."""
        self.warn('Reconnecting')
        self.stop()
        self.disconnect()
        self.connect()
        self.init_streaming()


class WiFiShieldHandler(asyncore.dispatcher_with_send):
    def __init__(self, sock, callback=None, high_speed=True,
                 parser=None, daisy=False):
        asyncore.dispatcher_with_send.__init__(self, sock)

        self.callback = callback
        self.daisy = daisy
        self.high_speed = high_speed
        self.last_odd_sample = OpenBCISample()
        self.parser = parser if parser is not None else ParseRaw(
            gains=[24, 24, 24, 24, 24, 24, 24, 24])

    def handle_read(self):
        # 3000 is the max data the WiFi shield is allowed to send over TCP
        data = self.recv(3000)
        if len(data) > 2:
            if self.high_speed:
                packets = int(len(data) / 33)
                raw_data_packets = []
                for i in range(packets):
                    raw_data_packets.append(
                        bytearray(data[i * 33: i * 33 + 33]))
                samples = self.parser.transform_raw_data_packets_to_sample(
                    raw_data_packets=raw_data_packets)

                for sample in samples:
                    # if a daisy module is attached, wait to concatenate two samples
                    # (main board + daisy) before passing it to callback
                    if self.daisy:
                        # odd sample: daisy sample, save for later
                        if ~sample.sample_number % 2:
                            self.last_odd_sample = sample
                        # even sample: concatenate and send if last sample was the first part,
                        #  otherwise drop the packet
                        elif sample.sample_number - 1 == self.last_odd_sample.sample_number:
                            # the aux data will be the average between the two samples, as the
                            # channel samples themselves have been averaged by the board
                            daisy_sample = self.parser.make_daisy_sample_object_wifi(
                                self.last_odd_sample, sample)
                            if self.callback is not None:
                                self.callback(daisy_sample)
                    else:
                        if self.callback is not None:
                            self.callback(sample)

            else:
                try:
                    possible_chunks = data.split('\r\n')
                    if len(possible_chunks) > 1:
                        possible_chunks = possible_chunks[:-1]
                    for possible_chunk in possible_chunks:
                        if len(possible_chunk) > 2:
                            chunk_dict = json.loads(possible_chunk)
                            if 'chunk' in chunk_dict:
                                for sample in chunk_dict['chunk']:
                                    if self.callback is not None:
                                        self.callback(sample)
                            else:
                                print("not a sample packet")
                except ValueError as e:
                    print("failed to parse: %s" % data)
                    print(e)
                except BaseException as e:
                    print(e)


class WiFiShieldServer(asyncore.dispatcher):

    def __init__(self, host, port, callback=None, gains=None, high_speed=True, daisy=False):
        asyncore.dispatcher.__init__(self)
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.set_reuse_addr()
        self.bind((host, port))
        self.daisy = daisy
        self.listen(5)
        self.callback = None
        self.handler = None
        self.parser = ParseRaw(gains=gains)
        self.high_speed = high_speed

    def handle_accept(self):
        pair = self.accept()
        if pair is not None:
            sock, addr = pair
            print('Incoming connection from %s' % repr(addr))
            self.handler = WiFiShieldHandler(sock, self.callback, high_speed=self.high_speed,
                                             parser=self.parser, daisy=self.daisy)

    def set_callback(self, callback):
        self.callback = callback
        if self.handler is not None:
            self.handler.callback = callback

    def set_daisy(self, daisy):
        self.daisy = daisy
        if self.handler is not None:
            self.handler.daisy = daisy

    def set_gains(self, gains):
        self.parser.set_ads1299_scale_factors(gains)

    def set_parser(self, parser):
        self.parser = parser
        if self.handler is not None:
            self.handler.parser = parser


class ParseRaw(object):
    def __init__(self,
                 board_type='cyton',
                 gains=None,
                 log=False,
                 micro_volts=False,
                 scaled_output=True):
        self.board_type = board_type
        self.gains = gains
        self.log = log
        self.micro_volts = micro_volts
        self.scale_factors = []
        self.scaled_output = scaled_output

        if gains is not None:
            self.scale_factors = self.get_ads1299_scale_factors(self.gains, self.micro_volts)

        self.raw_data_to_sample = RawDataToSample(gains=gains,
                                                  scale=scaled_output,
                                                  scale_factors=self.scale_factors,
                                                  verbose=log)

    def is_stop_byte(self, byte):
        """
        Used to check and see if a byte adheres to the stop byte structure
            of 0xCx where x is the set of numbers from 0-F in hex of 0-15 in decimal.
        :param byte: {int} - The number to test
        :return: {boolean} - True if `byte` follows the correct form
        """
        return (byte & 0xF0) == 0xC0

    def get_ads1299_scale_factors(self, gains, micro_volts=None):
        out = []
        for gain in gains:
            scale_factor = 4.5 / float((pow(2, 23) - 1)) / float(gain)
            if micro_volts is None:
                if self.micro_volts:
                    scale_factor *= 1000000.
            else:
                if micro_volts:
                    scale_factor *= 1000000.

            out.append(scale_factor)
        return out

    def get_channel_data_array(self, raw_data_to_sample):
        """
        :param raw_data_to_sample: RawDataToSample
        :return:
        """
        channel_data = []
        number_of_channels = len(raw_data_to_sample.scale_factors)
        daisy = number_of_channels == 16
        channels_in_packet = 8
        if not daisy:
            channels_in_packet = number_of_channels
        # Channel data arrays are always 8 long

        for i in range(channels_in_packet):
            counts = self.interpret_24_bit_as_int_32(
                raw_data_to_sample.raw_data_packet[
                    (i * 3) +
                    2:(i * 3) +
                    2 + 3
                ]
            )
            channel_data.append(
                raw_data_to_sample.scale_factors[i] *
                counts if raw_data_to_sample.scale else counts
            )

        return channel_data

    def get_data_array_accel(self, raw_data_to_sample):
        accel_data = []
        for i in range(3):
            counts = self.interpret_16_bit_as_int_32(
                raw_data_to_sample.raw_data_packet[
                26 +
                (i * 2): 26 + (i * 2) + 2])
            accel_data.append((0.002 / (pow(2, 4))) *
                              counts if raw_data_to_sample.scale else counts)
        return accel_data

    def get_raw_packet_type(self, stop_byte):
        return stop_byte & 0xF

    def interpret_16_bit_as_int_32(self, two_byte_buffer):
        return struct.unpack('>h', two_byte_buffer)[0]

    def interpret_24_bit_as_int_32(self, three_byte_buffer):
        # 3 byte ints
        unpacked = struct.unpack('3B', three_byte_buffer)

        # 3byte int in 2s compliment
        if unpacked[0] > 127:
            pre_fix = bytes(bytearray.fromhex('FF'))
        else:
            pre_fix = bytes(bytearray.fromhex('00'))

        three_byte_buffer = pre_fix + three_byte_buffer

        # unpack little endian(>) signed integer(i) (makes unpacking platform independent)
        return struct.unpack('>i', three_byte_buffer)[0]

    def parse_packet_standard_accel(self, raw_data_to_sample):
        """
        :param raw_data_to_sample: RawDataToSample
        :return:
        """
        # Check to make sure data is not null.
        if raw_data_to_sample is None:
            raise RuntimeError('Undefined or Null Input')
        if raw_data_to_sample.raw_data_packet is None:
            raise RuntimeError('Undefined or Null Input')

        # Check to make sure the buffer is the right size.
        if len(raw_data_to_sample.raw_data_packet) != 33:
            raise RuntimeError('Invalid Packet Byte Length')

        # Verify the correct stop byte.
        if raw_data_to_sample.raw_data_packet[0] != 33:
            raise RuntimeError('Invalid Start Byte')

        sample_object = OpenBCISample()

        sample_object.accel_data = self.get_data_array_accel(raw_data_to_sample)

        sample_object.channels_data = self.get_channel_data_array(raw_data_to_sample)

        sample_object.sample_number = raw_data_to_sample.raw_data_packet[
            1
        ]
        sample_object.start_byte = raw_data_to_sample.raw_data_packet[
            0
        ]
        sample_object.stop_byte = raw_data_to_sample.raw_data_packet[
            32
        ]

        sample_object.valid = True

        now_ms = int(round(time.time() * 1000))

        sample_object.timestamp = now_ms
        sample_object.boardTime = 0

        return sample_object

    def parse_packet_standard_raw_aux(self, raw_data_to_sample):
        pass

    def parse_packet_time_synced_accel(self, raw_data_to_sample):
        pass

    def parse_packet_time_synced_raw_aux(self, raw_data_to_sample):
        pass

    def set_ads1299_scale_factors(self, gains, micro_volts=None):
        self.scale_factors = self.get_ads1299_scale_factors(gains, micro_volts=micro_volts)

    def transform_raw_data_packet_to_sample(self, raw_data):
        """
        Used transform raw data packets into fully qualified packets
        :param raw_data:
        :return:
        """
        try:
            self.raw_data_to_sample.raw_data_packet = raw_data
            packet_type = self.get_raw_packet_type(raw_data[32])
            if packet_type == 0:
                sample = self.parse_packet_standard_accel(self.raw_data_to_sample)
            elif packet_type == 1:
                sample = self.parse_packet_standard_raw_aux(self.raw_data_to_sample)
            elif packet_type == 3 or \
                    packet_type == 4:
                sample = self.parse_packet_time_synced_accel(self.raw_data_to_sample)
            elif packet_type == 5 or \
                    packet_type == 6:
                sample = self.parse_packet_time_synced_raw_aux(self.raw_data_to_sample)
            else:
                sample = OpenBCISample()
                sample.error = 'This module does not support packet type %d' % packet_type
                sample.valid = False

            sample.packet_type = packet_type
        except BaseException as e:
            sample = OpenBCISample()
            if hasattr(e, 'message'):
                sample.error = e.message
            else:
                sample.error = e
            sample.valid = False

        return sample

    def make_daisy_sample_object_wifi(self, lower_sample_object, upper_sample_object):
        """
        /**
        * @description Used to make one sample object from two sample
        *      objects. The sample number of the new daisy sample will be the
        *      upperSampleObject's sample number divded by 2. This allows us
        *      to preserve consecutive sample numbers that flip over at 127
        *      instead of 255 for an 8 channel. The daisySampleObject will
        *      also have one `channelData` array with 16 elements inside it,
        *      with the lowerSampleObject in the lower indices and the
        *      upperSampleObject in the upper set of indices. The auxData from
        *      both channels shall be captured in an object called `auxData`
        *      which contains two arrays referenced by keys `lower` and
        *      `upper` for the `lowerSampleObject` and `upperSampleObject`,
        *      respectively. The timestamps shall be averaged and moved into
        *      an object called `timestamp`. Further, the un-averaged
        *      timestamps from the `lowerSampleObject` and `upperSampleObject`
        *      shall be placed into an object called `_timestamps` which shall
        *      contain two keys `lower` and `upper` which contain the original
        *      timestamps for their respective sampleObjects.
        * @param lowerSampleObject {Object} - Lower 8 channels with odd sample number
        * @param upperSampleObject {Object} - Upper 8 channels with even sample number
        * @returns {Object} - The new merged daisy sample object
        */
        """
        daisy_sample_object = OpenBCISample()

        if lower_sample_object.channels_data is not None:
            daisy_sample_object.channels_data = lower_sample_object.channels_data + \
                upper_sample_object.channels_data

        daisy_sample_object.sample_number = upper_sample_object.sample_number
        daisy_sample_object.id = daisy_sample_object.sample_number

        daisy_sample_object.aux_data = {
            'lower': lower_sample_object.aux_data,
            'upper': upper_sample_object.aux_data
        }

        if lower_sample_object.timestamp:
            daisy_sample_object.timestamp = lower_sample_object.timestamp

        daisy_sample_object.stop_byte = lower_sample_object.stop_byte

        daisy_sample_object._timestamps = {
            'lower': lower_sample_object.timestamp,
            'upper': upper_sample_object.timestamp
        }

        if lower_sample_object.accel_data:
            if lower_sample_object.accel_data[0] > 0 or lower_sample_object.accel_data[1] > 0 or \
                    lower_sample_object.accel_data[2] > 0:
                daisy_sample_object.accel_data = lower_sample_object.accel_data
            else:
                daisy_sample_object.accel_data = upper_sample_object.accel_data

        daisy_sample_object.valid = True

        return daisy_sample_object

    """
    /**
 * @description Used transform raw data packets into fully qualified packets
 * @param o {RawDataToSample} - Used to hold data and configuration settings
 * @return {Array} samples An array of {Sample}
 * @author AJ Keller (@aj-ptw)
 */
function transformRawDataPacketsToSample (o) {
  let samples = [];
  for (let i = 0; i < o.rawDataPackets.length; i++) {
    o.rawDataPacket = o.rawDataPackets[i];
    const sample = transformRawDataPacketToSample(o);
    samples.push(sample);
    if (sample.hasOwnProperty('sampleNumber')) {
      o['lastSampleNumber'] = sample.sampleNumber;
    } else if (!sample.hasOwnProperty('impedanceValue')) {
      o['lastSampleNumber'] = o.rawDataPacket[k.OBCIPacketPositionSampleNumber];
    }
  }
  return samples;
}
    """

    def transform_raw_data_packets_to_sample(self, raw_data_packets):
        samples = []

        for raw_data_packet in raw_data_packets:
            sample = self.transform_raw_data_packet_to_sample(raw_data_packet)
            samples.append(sample)
            self.raw_data_to_sample.last_sample_number = sample.sample_number

        return samples


class RawDataToSample(object):
    """Object encapulsating a parsing object."""

    def __init__(self,
                 accel_data=None,
                 gains=None,
                 last_sample_number=0,
                 raw_data_packets=None,
                 raw_data_packet=None,
                 scale=True,
                 scale_factors=None,
                 time_offset=0,
                 verbose=False):
        """
        RawDataToSample
        :param accel_data: list
            The channel settings array
        :param gains: list
            The gains of each channel, this is used to derive number of channels
        :param last_sample_number: int
        :param raw_data_packets: list
            list of raw_data_packets
        :param raw_data_packet: bytearray
            A single raw data packet
        :param scale: boolean
            Default `true`. A gain of 24 for Cyton will be used and 51 for ganglion by default.
        :param scale_factors: list
            Calculated scale factors
        :param time_offset: int
            For non time stamp use cases i.e. 0xC0 or 0xC1 (default and raw aux)
        :param verbose:
        """
        self.accel_data = accel_data if accel_data is not None else []
        self.gains = gains if gains is not None else []
        self.time_offset = time_offset
        self.last_sample_number = last_sample_number
        self.raw_data_packets = raw_data_packets if raw_data_packets is not None else []
        self.raw_data_packet = raw_data_packet
        self.scale = scale
        self.scale_factors = scale_factors if scale_factors is not None else []
        self.verbose = verbose


class OpenBCISample(object):
    """Object encapulsating a single sample from the OpenBCI board."""

    def __init__(self,
                 aux_data=None,
                 board_time=0,
                 channel_data=None,
                 error=None,
                 imp_data=None,
                 packet_type=0,
                 protocol='wifi',
                 sample_number=0,
                 start_byte=0,
                 stop_byte=0,
                 valid=True,
                 accel_data=None):
        self.aux_data = aux_data if aux_data is not None else []
        self.board_time = board_time
        self.channels_data = channel_data if aux_data is not None else []
        self.error = error
        self.id = sample_number
        self.imp_data = imp_data if aux_data is not None else []
        self.packet_type = packet_type
        self.protocol = protocol
        self.sample_number = sample_number
        self.start_byte = start_byte
        self.stop_byte = stop_byte
        self.timestamp = 0
        self._timestamps = {}
        self.valid = valid
        self.accel_data = accel_data if accel_data is not None else []
