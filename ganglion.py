from bluepy.btle import DefaultDelegate, Peripheral
import atexit
import sys
import warnings
import datetime
from bitstring import BitArray

SAMPLE_RATE = 200.0  # Hz

# service for communication, as per docs
BLE_SERVICE = "fe84"
# characteristics of interest
BLE_CHAR_RECEIVE = "2d30c082f39f4ce6923f3484ea480596"
BLE_CHAR_SEND = "2d30c083f39f4ce6923f3484ea480596"
BLE_CHAR_DISCONNECT = "2d30c084f39f4ce6923f3484ea480596"

class OpenBCIGanglion():

    def __init__(self, mac=None):
        if not mac:
            sys.exit('You need a Mac Address to find the Ganglion.')
        else:
            self.mac_address = mac

        self.streaming = False
        self.board_type = 'ganglion'

        atexit.register(self.disconnect)

        self.connect()

    def write_command(self, command):
        self.char_write.write(command)

    def connect(self):

        self.ganglion = Peripheral(self.mac_address, 'random')

        self.service = self.ganglion.getServiceByUUID(BLE_SERVICE)

        self.char_read = self.service.getCharacteristics(BLE_CHAR_RECEIVE)[0]

        self.char_write = self.service.getCharacteristics(BLE_CHAR_SEND)[0]

        self.char_discon = self.service.getCharacteristics(BLE_CHAR_DISCONNECT)[0]

        self.ble_delegate = GanglionDelegate()
        self.ganglion.setDelegate(self.ble_delegate)

        self.desc_notify = self.char_read.getDescriptors(forUUID=0x2902)[0]

        try:
            self.desc_notify.write(b"\x01")
        except Exception as e:
            print("Something went wrong while trying to enable notification: " + str(e))

        print("Connection established")

    def disconnect(self):
        pass

    def start_stream(self):

        if not self.streaming:
            self.streaming = True
            self.dropped_packets = 0
            self.write_command(b'b')

        else:
            while self.streaming:
                try:
                    self.ganglion.waitForNotifications(1./SAMPLE_RATE)
                except:
                    print('Something went wrong')



class GanglionDelegate(DefaultDelegate):
    def __init__(self):
        DefaultDelegate.__init___(self)

        self.last_values = np.array([0, 0, 0, 0])
        self.last_id = -1
        self.samples = []

    def handleNotification(self, cHandle, data):
        if len(data) < 1:
            warnings.warn('A packet should at least hold one byte...')
        self.parse_raw(data)

    def parse_raw(self, raw_data):
        if type(raw_data) == str:
            data = struct.unpack(str(len(packet)) + 'B', "".join(packet))
        else:
            data = raw_data

        print(data)
        self.push_sample(data)

    def push_sample(self, data):
        self.samples.append(data)

    def getSamples(self):
        old_samples = self.samples
        self.samples = []
        return old_samples

    def checked_dropped(self):
        pass

    def decompress_signed(self, bit_array):
        pass
