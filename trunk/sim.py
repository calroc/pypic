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
from util import initialize
from programmer import LOW_LEVEL, MID_LEVEL, Programmer
from myhdl import (
    Signal,
    Simulation,
    instance,
    traceSignals,
    StopSimulation,
    )
##import parallel


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
