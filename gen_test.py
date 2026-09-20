from instruments import open_generator

gen = open_generator()
try:
    print(gen.identify())
    gen.set_load(1, 50)                 # match cable termination; math.inf = High-Z
    gen.set_waveform(1, "sine", 60e6, 1)  # ch, shape, Hz, Vpp, offset=0, phase=0
    gen.output(1, True)
    print(gen.query_channel(1))
    gen.output(1, True)
finally:
    gen.close()