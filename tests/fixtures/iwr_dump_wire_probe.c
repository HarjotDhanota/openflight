#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dump_wire_trace.h"

#define L3_DUMP_WIRE_TRACE 1
#define L3_DUMP_STAGE_TRACE 1
#define L3_CAPTURE_EVIDENCE 1
typedef void *UART_Handle;
static int32_t fake_result;
static int reports;
static int32_t UART_writePolling(UART_Handle handle, uint8_t *data, uint32_t size)
{
    (void)handle; (void)data; (void)size;
    return fake_result;
}
#define CLI_write(...) (++reports)
#include "wire_hook.inc"

int main(int argc, char **argv)
{
    l3_wire_trace_t state;
    uint8_t block[1024];
    uint32_t i, size, calls = 0;
    int scenario;
    assert(argc == 2);
    scenario = atoi(argv[1]);
    l3_wire_reset(&state);
    for (i = 0; i < sizeof(block); i++) block[i] = (uint8_t)(i * 37U);
    for (size = 1; size <= sizeof(block); size *= 2) {
        int32_t result = (int32_t)size;
        calls++;
        if (calls == 3 && scenario == 1) result--;
        if (calls == 3 && scenario == 2) result = -9;
        if (calls == 3 && scenario == 3) result++;
        l3_wire_begin_write(&state, block, size);
        l3_wire_end_write(&state, size, result);
    }
    assert(state.calls == 11 && state.requested == 2047);
    assert(state.errors == (scenario == 2));
    assert(state.mismatches == (scenario != 0));
    assert(state.first_call == (scenario == 0 ? 0U : 3U));
    printf("%u %u %u %u %u %u %d %08x\n", state.calls, state.requested,
           state.returned, state.errors, state.mismatches, state.first_call,
           state.first_result, l3_wire_crc32(&state));
    l3_wire_reset(&state);
    assert(state.calls == 0 && state.requested == 0 && state.errors == 0);
    assert(l3_wire_crc32(&state) == 0);
    L3_WIRE_BEGIN();
    fake_result = 9;
    assert(UART_writePolling(NULL, block, 9) == 9);
    fake_result = -2;
    assert(UART_writePolling(NULL, block, 3) == -2);
    assert(gDumpWire.calls == 2 && gDumpWire.requested == 12 && gDumpWire.returned == 9);
    assert(gDumpWire.errors == 1 && gDumpWire.first_call == 2 && gDumpWire.first_result == -2);
    L3_WIRE_END();
    assert(reports == 1);
    return 0;
}
