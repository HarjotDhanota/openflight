#ifndef OPENFLIGHT_L3_FEATURE_CHECKS_H
#define OPENFLIGHT_L3_FEATURE_CHECKS_H

/* Feature-flag combinations the firmware cannot build correctly. l3_dump.c
 * includes this after deriving L3_CAPTURE_EVIDENCE, so an invalid set fails
 * the build with a reason instead of building something that half works.
 * tests/test_iwr6843_firmware_cli_table.py compiles each refusal. */

/* cp (clock_compact_handler.h) is included from clock_probe.h and shares its
 * boot token (gClockBootHi/Lo) and hex parser. */
#if defined(L3_USB_CLOCK_COMPACT) && !defined(L3_USB_CLOCK_PROBE)
#error "L3_USB_CLOCK_COMPACT requires L3_USB_CLOCK_PROBE: cp shares clockProbe's boot token and helpers"
#endif

#endif
