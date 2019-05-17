from pyOpenBCI import OpenBCICyton

def print_raw(sample):
    print(sample.channels_data)

board = OpenBCICyton()

board.start_stream(print_raw)
