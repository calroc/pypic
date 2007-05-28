'''
    PyPIC - A simple 12F675 PIC Programmer.
    Copyright (C) 2007 Simon Forman.

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
from myhdl import instance, intbv

OSCCAL_ADDRESS = 3 # Real value = 0x3ff
OSCCAL_MASK = intbv(15360)[14:] # 11110000000000
RETLW_MASK = intbv(13312)[14:]  # 11010000000000


class InvalidOSCCALError(Exception): pass
class FaultyWriteError(Exception): pass


def ProgramCycle(programmer, data):

    @instance
    def block():
        yield programmer.LoadProg(data)
        yield programmer.BeginProg()

        output = []
        yield programmer.ReadProg(output)
        if output[0] != data:
            raise FaultyWriteError

        yield programmer.IncrAddr()

    return block


def ReadOSCCAL(programmer):

    @instance
    def block():
        output = []

        yield programmer.reset()

        for _ in xrange(OSCCAL_ADDRESS):
            yield programmer.IncrAddr()

        yield programmer.ReadProg(output)

        OSCCAL = output[0]

##        if OSCCAL & OSCCAL_MASK != RETLW_MASK:
##            raise InvalidOSCCALError

        programmer.OSCCAL = OSCCAL

    return block
        

def ReadIDAndBandGap(programmer):

    @instance
    def block():
        output = []

        yield programmer.reset()

        yield programmer.LoadConf(intbv(0)[14:])

        for _ in range(4):
            yield programmer.ReadProg(output)
            yield programmer.IncrAddr()

        for _ in range(3):
            yield programmer.IncrAddr()

        yield programmer.ReadProg(output)

        ID, BG = tuple(output[:4]), output[-1]

        programmer.ID = ID
        programmer.BG = BG

    return block


def BulkEraseDevice(programmer):
    @instance
    def block():
        for attr in ('OSCCAL', 'ID', 'BG'):
            if not hasattr(programmer, attr):
                raise Exception('aw %s' % repr(attr))
        yield programmer.EraseProg()
        yield programmer.EraseData()
    return block


def CleanDevice(programmer):
    @instance
    def block():
        yield ReadOSCCAL(programmer)
        yield ReadIDAndBandGap(programmer)
        yield BulkEraseDevice(programmer)
    return block























