#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

enum { UART_DATA_TEXT = 1, UART_DATA_BINARY = 2, UART_ECHO_OFF = 0, UART_ECHO_ON = 1 };
typedef struct {
    uint32_t clockFrequency, baudRate;
    int writeDataMode, readDataMode, readReturnMode, readEcho;
    int isPinMuxDone, dataLength, stopBits, parityType;
} UART_Params;
static uint32_t gCpuClock = 200000000;
static void *gCliUart;
static UART_Params opened;

static void UART_Params_init(UART_Params *params)
{
    memset(params, 0, sizeof(*params));
    params->readDataMode = UART_DATA_TEXT;
    params->readReturnMode = 1;
    params->readEcho = UART_ECHO_ON;
    params->dataLength = 8;
    params->stopBits = 1;
}

static void *UART_open(int index, UART_Params *params)
{
    assert(index == 0);
    opened = *params;
    return &opened;
}

int main(int argc, char **argv)
{
    UART_Params uartParams;
    assert(argc == 2);
#include "uart_init.inc"
    assert(gCliUart == &opened);
    assert(opened.readEcho == atoi(argv[1]));
    assert(opened.clockFrequency == 200000000 && opened.baudRate == 1041667);
    assert(opened.writeDataMode == UART_DATA_BINARY);
    assert(opened.readDataMode == UART_DATA_TEXT && opened.readReturnMode == 1);
    assert(opened.isPinMuxDone == 1);
    assert(opened.dataLength == 8 && opened.stopBits == 1 && opened.parityType == 0);
    return 0;
}
