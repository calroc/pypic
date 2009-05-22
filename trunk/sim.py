#!/usr/bin/env python
'''
    PyPIC - A simple 12F675 PIC Programmer.
    Copyright (C) 2007, 2008, 2009 Simon Forman.

    PyPIC is free software; you can redistribute it and/or modify it
    under the terms of the GNU General Public License as published by the
    Free Software Foundation; either version 2 of the License, or (at
    your option) any later version.

    This program is distributed in the hope that it will be useful, but
    WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
    General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program; if not, write to the Free Software
    Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
    02110-1301, USA.
'''
import logging
from myhdl import (
    delay,
    always,
    now,
    intbv,
    bin,
    enum,
    join,
    Signal,
    Simulation,
#    instance,
    traceSignals,
    StopSimulation,
    )
##import parallel


def instance(function):
    return function()


logging.basicConfig(format='%(message)s', level=logging.DEBUG)
log = logging.getLogger()


_onoff = lambda n: ('off', 'on')[bool(n)]


# When we send data to the chip, we want to send a zero start bit and
# stop bit.  To do this, we accept 8- or 14-bit data value, shift it one
# bit left and AND it with the SIXTEEN_BITS "constant".
SIXTEEN_BITS = intbv(0)[16:]


# These two state enums are used by the lowlevel and midlevel api mixins
# to let the GTKWave program show the state transitions.
#
# The lowest level is either at rest, or sending or receiving data.
LOW_LEVEL = enum('Tx', 'Rx', 'REST')

# The midlevel corresponds directly to the various programming commands
# as specified in 41191D.pdf.  LC = LoadConfig; RD = ReadDataMemory; &c..
MID_LEVEL = enum(*"""

    naught

    LC
    LP
    LD
    Incr
    EOP
    RP
    RD
    BP
    EP
    ED

""".split())


OSCCAL_ADDRESS = 3 # Real value = 0x3ff
OSCCAL_MASK = intbv(int('11110000000000', 2))[14:]
RETLW_MASK = intbv(int('11010000000000', 2))[14:]


class InvalidOSCCALError(Exception): pass
class FaultyWriteError(Exception): pass


# Now we need the actual bit patterns of the various commands in the form
# of intbv instances.  To make these conveniently, I made this little
# _command() function.

def _command(pattern):
    '''
    Convert a bit pattern string into an intbv.
    '''
    res = intbv(0)[6:]
    for i, bit in enumerate(reversed(pattern.split())):
        if bit == '1':
            res[i] = True
    return res


def _print_commands():
    '''
    Debugging aid to print out the programmer commands defined below.
    '''
    for n, obj in globals().iteritems():
        if isinstance(obj, intbv):
            print '%s = %s' % (n, bin(obj, 6))

# And here they are, the basic commands to program the PIC 12F675.

# Command and 14 bits of data
LoadConfiguration        = _command('X X 0 0 0 0')
LoadDataforProgramMemory = _command('X X 0 0 1 0')

# No data bits.
IncrementAddress = _command('X X 0 1 1 0')
EndProgramming   = _command('0 0 1 0 1 0')

# Command and 8 bits of data
LoadDataforDataMemory = _command('X X 0 0 1 1')

# Read 14 and 8 bits, respectively, from the port.
ReadDatafromProgramMemory = _command('X X 0 1 0 0')
ReadDatafromDataMemory    = _command('X X 0 1 0 1')

# Require Tprog.
BeginProgrammingInternallyTimed = _command('0 0 1 0 0 0')
BeginProgrammingExternallyTimed = _command('0 1 1 0 0 0')

# Require Terase.
BulkEraseProgramMemory = _command('X X 1 0 0 1')
BulkEraseDataMemory    = _command('X X 1 0 1 1')


def _bus2int(bus):
    res = sum(1 << n for n, line in enumerate(reversed(bus)) if line.val)
    return res


def ClockDriver(clock):
    log.info('P+DS\t\t\t%s\tclock', str(now()))

    @always(delay(10))
    def driveClk():
        next = not clock
        log.info('\t\t\t%s\t%s', str(now()), _onoff(next))
        clock.next = next

    return driveClk


def StrobeClockLink(clock, STROBE_BIT, strobe_enable):
    '''Drive the chip strobe from the sim clock.'''

    @instance
    def clock_link():
        while True:
            yield clock.posedge
            STROBE_BIT.next = strobe_enable and True
            yield clock.negedge
            STROBE_BIT.next = False

    return clock_link


def PortDriver(clock, bus):
    prev = [-1]

    @always(clock.posedge, clock.negedge)
    def drive_port():
        b = _bus2int(bus)
        if b != prev[0]:
            prev[0] = b
            send(b)

    return drive_port


def initialize(
    clock,
    STROBE_BIT,
    DATA_BIT,
    POWER_BIT,
    PROGRAM_BIT,
    strobe_enable
    ):
    return (
        ClockDriver(clock),
        StrobeClockLink(clock, STROBE_BIT, strobe_enable),
        PortDriver(
            clock,
            (PROGRAM_BIT, POWER_BIT, DATA_BIT, STROBE_BIT)
            )
        )


# The lowest level of programming the PIC involves sending and receiving
# data over a serial connection to and from the chip.  So we create a
# base class that provides just those two calls, and a "rest" call that
# turns off the chip strobe and brings the data line low to support
# internally timed "wait states" such as when erasing the memories.

class LowerController:

    def __init__(self,
        clock,
        state,
        mstate,
        STROBE_BIT,
        DATA_BIT,
        POWER_BIT,
        PROGRAM_BIT,
        strobe_enable
        ):
        self.clock = clock
        self.state = state
        self.mstate = mstate
        self.STROBE_BIT = STROBE_BIT
        self.DATA_BIT = DATA_BIT
        self.POWER_BIT = POWER_BIT
        self.PROGRAM_BIT = PROGRAM_BIT
        self.strobe_enable = strobe_enable

    def sendBits(self, bits):
        '''
        Send the bits, LSB (index -1) to MSB (index 0) serial-ly over the
        wire.
        '''
        # Iteration is wonky on intbv's.
        C = reversed(list(bits))

        # Set the first bit on the data line and the lowlevel state.
        yield self.STROBE_BIT.posedge
        self.state.next = LOW_LEVEL.Tx
        self.DATA_BIT.next = C.next()

        # Send the rest of the data bits.
        for bit in C:
            yield self.STROBE_BIT.posedge
            self.DATA_BIT.next = bit

    def readBits(self, number_of_bits):
        '''
        Reads number_of_bits bits from the wire, and puts a list of bits,
        LSB to MSB, into the LowerController instance's 'res' attribute.
        '''

        # Set the state,
        yield self.STROBE_BIT.posedge
        self.state.next = LOW_LEVEL.Rx

        # Accumulate the requested number_of_bits.
        res = []
        for _ in range(number_of_bits):

            # Read on the negative edge.
            yield self.STROBE_BIT.negedge

            # We also put the bit into the data line in order to see it
            # on the GTKWave traces.
            bit = self.DATA_BIT.next = self._read()
            res.append(bit)

        self.res = res

    def rest(self, cycles=1):
        '''
        Pause the programmer and leave the strobe low for cycles cycles.
        '''
        if cycles < 1:
            return

        self.strobe_enable.next = False
        yield self.clock.posedge

        self.state.next = LOW_LEVEL.REST
        self.DATA_BIT.next = False
        cycles -= 1
        yield self.clock.negedge

        while self.state == LOW_LEVEL.REST and cycles:
            yield self.clock.posedge
            cycles -= 1

        self.strobe_enable.next = True

    def _read(self):
        return self.DATA_BIT.val


# There are a few different kinds of programmer commands: send a command;
# send a command with data; send a command and read data; etc...
#
# The next stage of the programmer api makes these command types out of
# the lowlevel api above.  As you'll see, they are very basic.

class ProgrammingCommandTypesMixin:
    '''
    Implements the low-level programmer commands on top of the "api"
    exposed by the LowerController.
    '''

    def sendCommandAndData(self, cmd, data):
        assert cmd.max == 64
        assert data.max == 16384

        # Add start and stop bits.
        data = (data << 1 | SIXTEEN_BITS)[16:]

        @instance
        def sender():
            yield self.sendBits(cmd)
            yield self.sendBits(data)
        return sender

    def sendCommandAndByteData(self, cmd, data):
        assert cmd.max == 64
        assert data.max == 256

        # Add start, stop and pad bits.
        data = (data << 1 | SIXTEEN_BITS)[16:]

        @instance
        def sender():
            yield self.sendBits(cmd)
            yield self.sendBits(data)
        return sender

    def sendCommand(self, cmd):
        assert cmd.max == 64
        @instance
        def sender():
            yield self.sendBits(cmd)
        return sender

    def sendCommandAndReadData(self, cmd, output):
        assert cmd.max == 64

        @instance
        def sender():
            yield self.sendBits(cmd)
            yield self.readBits(16)
            res = self.res
            assert not (res[0] or res[15])

            N = sum(1 << n for n, bit in enumerate(res[1:-1]) if bit)
            output.append(N)
        return sender

    def sendCommandAndReadByteData(self, cmd, output):
        assert cmd.max == 64

        @instance
        def sender():
            yield self.sendBits(cmd)
            yield self.readBits(16)
            res = self.res
            assert not (res[0] or res[15])

            N = sum(1 << n for n, bit in enumerate(res[1:-7]) if bit)
            output.append(N)
        return sender

    def Tprog(self):
        @instance
        def sender():
            yield self.rest(3)
        return sender

    def Terase(self):
        @instance
        def sender():
            yield self.rest(3)
        return sender


def metaD(label):
    '''
    Return a decorator that will wrap a method with a "state-changer"
    block that will set the midlevel state to the label state.
    '''
    def D(func):
        '''
        Wrap a method that returns one or more MyHDL blocks with a new
        function that returns them AND another block that will set the
        midlevel state in parallel with the wrapped method's blocks.
        '''
        def newf(self, *a, **b):
            
            # Get the wrapped method's blocks.
            blocks = func(self, *a, **b)

            # Create a new "state-changer" block.
            @instance
            def state_change():
                yield self.clock.posedge
                self.mstate.next = label
                print label

            # Let them run in parallel.  (We must use join() rather than
            # just returning both in order that the "outer" command, the
            # one that called the wrapped method, doesn't continue as
            # soon as our new "state-changer" block completes.  Instead,
            # because of join(), the outer block will wait on both blocks
            # before continuing.  It was fun figuring that out.)
            return join(blocks, state_change)
        return newf
    return D


# So now we come to the meat and potatoes of the programmer, the actual
# useful highlevel api.  This mixin class uses the intbv's and
# metadecorator defined above to provide the basic commands to program
# the chip.  With these, we can build the highlevel blocks as shown in
# the flowcharts in the chip programming PDF.
#
# One thing I really liked about this design is how easy and
# straightforward the highlevel commands are to implement due mostly to
# the nice encapsulation and factoring provided by the above lower-level
# layers of the api.  Most of the commands below are one-liners (two-
# liners if you count the metadecorator invocations.

class ProgrammingCommandsMixin:
    '''
    Maps the programmer commands onto the lower-level command "types".
    '''

    @metaD(MID_LEVEL.LC)
    def LoadConf(self, data):
        return self.sendCommandAndData(LoadConfiguration, data)

    @metaD(MID_LEVEL.LP)
    def LoadProg(self, data):
        return self.sendCommandAndData(LoadDataforProgramMemory, data)

    @metaD(MID_LEVEL.LD)
    def LoadData(self, data):
        return self.sendCommandAndByteData(LoadDataforDataMemory, data)

    @metaD(MID_LEVEL.Incr)
    def IncrAddr(self):
        return self.sendCommand(IncrementAddress)

    @metaD(MID_LEVEL.EOP)
    def EOP(self):
        return self.sendCommand(EndProgramming)

    @metaD(MID_LEVEL.RP)
    def ReadProg(self, output):
        return self.sendCommandAndReadData(ReadDatafromProgramMemory, output)

    @metaD(MID_LEVEL.RD)
    def ReadData(self, output):
        return self.sendCommandAndReadByteData(ReadDatafromDataMemory, output)

    @metaD(MID_LEVEL.BP)
    def BeginProg(self):
        @instance
        def beginprog():
            yield self.sendCommand(BeginProgrammingInternallyTimed)
            yield self.Tprog()
        return beginprog

    @metaD(MID_LEVEL.EP)
    def EraseProg(self):
        @instance
        def eraseprog():
            yield self.sendCommand(BulkEraseProgramMemory)
            yield self.Terase()
        return eraseprog

    @metaD(MID_LEVEL.ED)
    def EraseData(self):
        @instance
        def erasedata():
            yield self.sendCommand(BulkEraseDataMemory)
            yield self.Terase()
        return erasedata


class MetaCommands:

    def start(self):
        @instance
        def FireItUp():

            print 'STARTUP COMMENCING'

            # Reset the bus.
            self.STROBE_BIT.next = False
            self.DATA_BIT.next = False
            self.POWER_BIT.next = False
            self.PROGRAM_BIT.next = False

            # Activate programmer mode.
            yield self.clock.posedge
            self.PROGRAM_BIT.next = True

            # Set the state vars.
            self.state.next = LOW_LEVEL.REST
            self.mstate.next = MID_LEVEL.naught

            # Turn on the chip.
            yield self.clock.posedge
            self.POWER_BIT.next = True

            # Activate the chip strobe.
            yield self.clock.posedge
            self.strobe_enable.next = True
            print 'STARTUP FINISHED'
        return FireItUp

    def shutdown(self):
        @instance
        def ShutErDown():

            print 'SHUTDOWN COMMENCING'

            yield self.clock.posedge

            # Set the state vars.
            self.state.next = LOW_LEVEL.REST
            self.mstate.next = MID_LEVEL.naught

            self.DATA_BIT.next = False
            self.strobe_enable.next = False

            # Deactivate programmer mode.
            yield self.clock.posedge
            self.PROGRAM_BIT.next = False

            # Reset the power.
            yield self.clock.posedge
            self.POWER_BIT.next = False

            yield self.clock.posedge
            print 'SHUTDOWN FINISHED'
        return ShutErDown

    def reset(self):
        @instance
        def Resetter():
            print 'RESET COMMENCING'
            yield self.shutdown()
            yield self.start()
            print 'RESET FINISHED'
        return Resetter

    def programCycle(self, data):
        @instance
        def block():
            yield self.LoadProg(data)
            yield self.BeginProg()

            output = []
            yield self.ReadProg(output)
            if output[0] != data:
                raise FaultyWriteError

            yield self.IncrAddr()

        return block

    def readOSCCAL(self):
        @instance
        def block():
            output = []

            yield self.reset()

            for _ in xrange(OSCCAL_ADDRESS):
                yield self.IncrAddr()

            yield self.ReadProg(output)

            OSCCAL = output[0]

    ##        if OSCCAL & OSCCAL_MASK != RETLW_MASK:
    ##            raise InvalidOSCCALError

            self.OSCCAL = OSCCAL

        return block

    def readIDAndBandGap(self):
        @instance
        def block():
            output = []

            yield self.reset()

            yield self.LoadConf(intbv(0)[14:])

            for _ in range(4):
                yield self.ReadProg(output)
                yield self.IncrAddr()

            for _ in range(3):
                yield self.IncrAddr()

            yield self.ReadProg(output)

            ID, BG = tuple(output[:4]), output[-1]

            self.ID = ID
            self.BG = BG

        return block

    def bulkEraseDevice(self):
        @instance
        def block():
            for attr in ('OSCCAL', 'ID', 'BG'):
                if not hasattr(self, attr):
                    raise Exception('aw %s' % repr(attr))
            yield self.EraseProg()
            yield self.EraseData()
        return block

    def cleanDevice(self):
        @instance
        def block():
            yield self.readOSCCAL()
            yield self.readIDAndBandGap()
            yield self.bulkEraseDevice()
        return block


class Programmer(
    LowerController,
    ProgrammingCommandTypesMixin,
    ProgrammingCommandsMixin,
    MetaCommands
    ):
    pass


def send(data):
    '''sends data to a parallel port.'''
    data = intbv(data)[4:]
    log.info('%s sent\t\t%s' % (bin(data, 4), str(now())))


def main():
    (
        STROBE_BIT,
        DATA_BIT,
        POWER_BIT,
        PROGRAM_BIT,

        clock,
        strobe_enable,

    ) = [Signal(False) for _ in range(6)]

    state = Signal(LOW_LEVEL.REST)
    mstate = Signal(MID_LEVEL.naught)

    init_blocks = initialize(
        clock,
        STROBE_BIT,
        DATA_BIT,
        POWER_BIT,
        PROGRAM_BIT,
        strobe_enable
        )

    programmer = Programmer(
        clock,
        state,
        mstate,
        STROBE_BIT,
        DATA_BIT,
        POWER_BIT,
        PROGRAM_BIT,
        strobe_enable,
        )

    @instance
    def Program():
        yield programmer.cleanDevice()
        yield programmer.shutdown()
        raise StopSimulation

    return init_blocks, Program


if __name__ == '__main__':
#    ts = traceSignals(main)
#    sim = Simulation(ts)

    (c, cl, pd), p = main()
    compenents = c, cl, pd, p
    sim = Simulation(compenents)
    sim.run()
