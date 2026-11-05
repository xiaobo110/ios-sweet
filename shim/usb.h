/*
 * K60 (SM8475) bare-metal USB device-mode log channel.
 *
 * The shim has no console: fastboot has gone away by the time we run, and the
 * panel needs a DSC encoder we do not have. So we bring the DWC3 controller up
 * as a USB device and stream text to the host over a vendor-specific bulk IN
 * endpoint; the host reads it with tools like librsb/pyusb.
 *
 * Ported from the ios-sweet kernel driver (xnu/patches/0072..0077), which was
 * validated on SM7150: ABL leaves the USB3 wrapper, the PHY and the VBUS
 * session alive, so the shim only has to power/clock the block if needed,
 * restart the device controller, and enumerate.
 */
#pragma once

#include <stdint.h>

/* 0 = controller + log channel up, negative = failure stage
 *   -1 GDSC never came up
 *   -2 a USB clock never halted
 *   -3 DWC3 global soft reset stuck
 *   -4 endpoint command failed
 *   -5 controller never left the halted state
 */
int usb_log_init(void);

/* Last values the USB driver read, for diagnostics on the panel. */
extern uint32_t usb_dbg[8];

/* eUSB2 PHY (0x088e3000) power-up + init; 0 on success, -1 if it stays silent. */
int usb_phy_init(void);
void usb_phy_snapshot(uint32_t *out);

/* Drain the event buffer and keep the control state machine going.
 * Must be called regularly (busy loop is fine). */
void usb_poll(void);

/* 1 once the host has issued SET_CONFIGURATION. */
int usb_log_ready(void);

/* Poll until the host configured us, or 'ms' elapse. Returns 1 on success. */
int usb_log_wait_ready(unsigned ms);

/* Queue text for the bulk IN endpoint. Data is batched in a small ring and
 * pushed by usb_poll(); returns the number of bytes accepted. */
unsigned usb_log_write(const char *s, unsigned len);
unsigned usb_log_puts(const char *s);

/* Keep the link alive for 'ms', polling events and flushing the ring. */
void usb_log_hold(unsigned ms);
