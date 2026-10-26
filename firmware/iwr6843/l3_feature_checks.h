#ifndef OPENFLIGHT_L3_FEATURE_CHECKS_H
#define OPENFLIGHT_L3_FEATURE_CHECKS_H

/* Feature-flag combinations the firmware cannot build correctly. l3_dump.c
 * includes this after deriving L3_CAPTURE_EVIDENCE, so an invalid set fails
 * the build with a reason instead of building something that half works.
 * tests/test_iwr6843_firmware_cli_table.py compiles each refusal. */

/* The clock handlers read l3_ticks(), the PMU cycle counter that only the
 * capture-evidence block defines and starts (l3_cycleCounterInit). */
#if defined(L3_USB_CLOCK_PROBE) && !defined(L3_CAPTURE_EVIDENCE)
#error "L3_USB_CLOCK_PROBE requires L3_CAPTURE_EVIDENCE (CONFIGURABLE_CAPTURE + HYBRID_CADENCE_CAPTURE): it reads l3_ticks"
#endif

/* cp (clock_compact_handler.h) is included from clock_probe.h and shares its
 * boot token (gClockBootHi/Lo) and hex parser. */
#if defined(L3_USB_CLOCK_COMPACT) && !defined(L3_USB_CLOCK_PROBE)
#error "L3_USB_CLOCK_COMPACT requires L3_USB_CLOCK_PROBE: cp shares clockProbe's boot token and helpers"
#endif

/* The two-round transfer serves a frozen capture and must keep its per-frame
 * sequence/timing/overflow evidence, which only the configurable
 * hybrid-cadence build records (L3_CAPTURE_EVIDENCE, dump v8/v9). */
#if defined(L3_TWO_ROUND) && !defined(L3_CAPTURE_EVIDENCE)
#error "L3_TWO_ROUND requires L3_CAPTURE_EVIDENCE (CONFIGURABLE_CAPTURE + HYBRID_CADENCE_CAPTURE)"
#endif

#endif
