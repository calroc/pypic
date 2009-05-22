#!/usr/bin/env python
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
import logging
from myhdl import (
    delay,
    always,
    now,
    intbv,
    bin,
    Signal,
    Simulation,
    instance,
    traceSignals,
    StopSimulation,
    )
from programmer import LOW_LEVEL, MID_LEVEL, Programmer
##import parallel


logging.basicConfig(format='%(message)s', level=10)
log = logging.getLogger()


_onoff = lambda n: ('off', 'on')[bool(n)]


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
    ts = traceSignals(main)
    sim = Simulation(ts)
    sim.run()
