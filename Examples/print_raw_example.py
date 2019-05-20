from pyOpenBCI import OpenBCICyton

def print_raw(sample):
    print(sample.channels_data)

#Set (daisy = True) to stream 16 ch 
board = OpenBCICyton(daisy = False)

board.start_stream(print_raw)
