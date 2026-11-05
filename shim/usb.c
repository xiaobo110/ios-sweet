/*
 * K60 (SM8475) bare-metal USB device-mode log channel - implementation.
 *
 * Register facts taken from the device (dump/live.dts + dump/kmod/msm_drm.ko +
 * clkref/gcc-sm8450.c):
 *
 *   DWC3 core register block   0x0a600000 (reg = 0xd93c, node ssusb@a600000)
 *   QSCRATCH (wrapper)         0x0a6f8800  (core base + 0xf8800)
 *       +0x10 HS_PHY_CTRL  bits 20 UTMI_OTG_VBUS_VALID, 28 SW_SESSVLD_SEL
 *       +0x30 SS_PHY_CTRL  bit  24 LANE0_PWR_PRESENT
 *   eUSB2 PHY                  0x088e3000  (qcom,usb-snps-eusb2-phy)
 *   GCC (0x00100000):
 *       0x49004 USB30_PRIM GDSC
 *       0x49018 master clk, 0x49020 sleep clk, 0x49024 mock_utmi clk
 *       0x49084 cfg_noc axi clk, 0x49088 aggre axi clk
 *   maximum-speed = "high-speed": the SS PHY is unused, so a USB2 gadget is
 *   enough.
 *
 * The control-transfer state machine follows the Synopsys DWC3 databook as
 * used by Linux drivers/usb/dwc3 (gadget.c, ep0.c) and matches the SM7150
 * driver that already enumerates on this project.
 */
#include <stdint.h>
#include "usb.h"

#ifndef NULL
#define NULL ((void *)0)
#endif

#define rd32(a)		(*(volatile uint32_t *)(uintptr_t)(a))
#define wr32(a, v)	(*(volatile uint32_t *)(uintptr_t)(a) = (uint32_t)(v))

static uint64_t usb_now(void)
{
	uint64_t v;

	__asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(v));
	return v;
}

static uint64_t usb_freq(void)
{
	uint64_t v;

	__asm__ volatile("mrs %0, cntfrq_el0" : "=r"(v));
	return v ? v : 19200000;
}

static void usb_delay_ms(unsigned ms)
{
	uint64_t f = usb_freq();
	uint64_t e = usb_now() + f / 1000u * ms;

	while (usb_now() < e)
		;
}

/* ---- DWC3 register map ------------------------------------------------- */
#define DWC3_BASE		0x0a600000UL
#define QSCRATCH_BASE		0x0a6f8800UL

#define GCTL			0x0c110
#define GSNPSID			0x0c120
#define GEVNTADRLO		0x0c400
#define GEVNTADRHI		0x0c404
#define GEVNTSIZ		0x0c408
#define GEVNTCOUNT		0x0c40c
#define DCFG			0x0c700
#define DCTL			0x0c704
#define DEVTEN			0x0c708
#define DSTS			0x0c70c
#define DALEPENA		0x0c720
#define DEPCMDPAR2(n)		(0x0c800 + 0x10 * (n))
#define DEPCMDPAR1(n)		(0x0c804 + 0x10 * (n))
#define DEPCMDPAR0(n)		(0x0c808 + 0x10 * (n))
#define DEPCMD(n)		(0x0c80c + 0x10 * (n))

#define GCTL_CORESOFTRESET	(1u << 11)
#define GCTL_PRTCAP_SHIFT	12
#define GCTL_PRTCAP_HOST	1u
#define GCTL_PRTCAP_DEVICE	2u
#define GCTL_PRTCAP_OTG		3u

#define GUSB2PHYCFG(n)		(0x0c200u + 4u * (n))	/* DWC3 device PHY config */
#define GUSB2PHYCFG_PHYSOFTRST	(1u << 31)

#define DCTL_RUN_STOP		(1u << 31)
#define DCTL_CSFTRST		(1u << 30)
#define DSTS_DEVCTRLHLT		(1u << 22)

#define DEPCMD_SETEPCONFIG	1
#define DEPCMD_SETTRANSFRESOURCE 2
#define DEPCMD_SETSTALL		4
#define DEPCMD_STARTTRANSFER	6
#define DEPCMD_DEPSTARTCFG	9
#define DEPCMD_CMDACT		(1u << 10)

#define TRB_HWO			(1u << 0)
#define TRB_LST			(1u << 1)
#define TRB_ISP_IMI		(1u << 10)
#define TRB_IOC			(1u << 11)
#define TRBCTL_SETUP		(2u << 4)
#define TRBCTL_STATUS2		(3u << 4)
#define TRBCTL_STATUS3		(4u << 4)

/* ---- GCC block (power + clocks) ---------------------------------------- */
#define GCC_BASE		0x00100000UL
#define GCC_USB30_PRIM_GDSC	(GCC_BASE + 0x49004)
#define GCC_USB30_PRIM_MASTER	(GCC_BASE + 0x49018)
#define GCC_USB30_PRIM_SLEEP	(GCC_BASE + 0x49020)
#define GCC_USB30_PRIM_UTMI	(GCC_BASE + 0x49024)
#define GCC_USB30_PRIM_CFGNOC	(GCC_BASE + 0x49084)
#define GCC_USB30_PRIM_AGGRE	(GCC_BASE + 0x49088)

#define GDSC_PWR_ON		(1u << 31)
#define GDSC_PWR_STS		(1u << 31)

/* ---- DMA area ----------------------------------------------------------- */
/*
 * The DMA area must sit inside the DRAM our device tree declares
 * (0x80000000..0xB9200000). 0x0ba00000 is past that end, so the first access
 * faulted with far=0xba010000. Keep it just above the kernel payload.
 */
/*
 * Must be memory the AP can actually reach. 0x0ba00000 faulted (past the
 * declared DRAM end) and 0x08380000 faulted too (reserved window), so use the
 * high window right after the payload, which the shim already executes from.
 */
#define USB_DMA_PA		0x0f8ff0000UL
#define USB_DMA_SIZE		0x4000

#define UD_EVT			0x0000	/* 4 KiB event buffer */
#define UD_EVT_SIZE		0x1000
#define UD_TRB_OUT		0x1000
#define UD_TRB_IN0		0x1010
#define UD_TRB_BULK		0x1020
#define UD_SETUP		0x1040
#define UD_DATA			0x1100	/* 512 byte control data stage */
#define UD_TX			0x1400	/* 2 KiB log queue */
#define UD_TX_SIZE		0x800
#define UD_TX_ACT		0x1c00	/* 512 B in-flight chunk (never rewritten) */
#define UD_TX_ACT_SIZE		512

/* DWC3 DEPCMD/event slot for a physical endpoint: ep0 OUT = 0, ep0 IN = 1,
 * then 2*ep + dir. Our log endpoint is 0x81 (EP1 IN) -> slot 3. */
#define BULK_DEPCMD_EP		3
#define BULK_EVT_EP		3
#define DALEPENA_EP0_AND_BULK	((1u << 0) | (1u << 1) | (1u << BULK_DEPCMD_EP))

/* Vendor specific: 1 interface, 1 bulk IN endpoint (0x81), 512 byte packets */
#define USB_VID			0x1209
#define USB_PID			0x0002

extern uint32_t usb_dbg[8];
uint32_t usb_dbg[8];

static struct {
	uint32_t evt_pos;
	uint32_t events;
	int state;
	int configured;
	int bulk_busy;
	unsigned tx_len;
	unsigned tx_inflight;
} u;

enum { EP0_SETUP, EP0_DATA, EP0_WAIT_STATUS2, EP0_WAIT_STATUS3, EP0_STATUS };

static const uint8_t dev_desc[18] = {
	18, 1, 0x00, 0x02, 0x00, 0x00, 0x00, 64,
	0x09, 0x12,		/* idVendor 0x1209 */
	0x02, 0x00,		/* idProduct 0x0002 */
	0x00, 0x01,		/* bcdDevice */
	1, 2, 3, 1,
};

/* config + interface + bulk IN endpoint */
static const uint8_t cfg_desc[] = {
	9, 2, 9 + 9 + 7, 0, 1, 1, 0, 0x80, 250,
	9, 4, 0, 0, 1, 0xff, 0x00, 0x00, 0,
	7, 5, 0x81, 0x02, 0x00, 0x02, 0,	/* bulk IN, 512 bytes */
};

static uint32_t usb_string(unsigned idx, uint8_t *out)
{
	const char *s;
	uint32_t n = 2;

	if (idx == 0) {
		out[0] = 4;
		out[1] = 3;
		out[2] = 0x09;
		out[3] = 0x04;
		return 4;
	}
	/* No table of pointers: the image must stay free of absolute
	 * relocations, so the literal is selected with a switch. */
	switch (idx) {
	case 1:
		s = "ios-sweet-k60";
		break;
	case 2:
		s = "K60 shim log channel";
		break;
	case 3:
		s = "0001";
		break;
	default:
		return 0;
	}
	for (; *s && n < 126; s++) {
		out[n++] = (uint8_t)*s;
		out[n++] = 0;
	}
	out[0] = (uint8_t)n;
	out[1] = 3;
	return n;
}

static void dwc_wr(uint32_t off, uint32_t v)
{
	wr32(DWC3_BASE + off, v);
	__asm__ volatile("dsb sy" ::: "memory");
}

static uint32_t dwc_rd(uint32_t off)
{
	return rd32(DWC3_BASE + off);
}

static int epcmd(unsigned ep, uint32_t cmd, uint32_t p0, uint32_t p1, uint32_t p2)
{
	dwc_wr(DEPCMDPAR0(ep), p0);
	dwc_wr(DEPCMDPAR1(ep), p1);
	dwc_wr(DEPCMDPAR2(ep), p2);
	dwc_wr(DEPCMD(ep), cmd | DEPCMD_CMDACT);
	for (int i = 0; i < 2000; i++) {
		uint32_t v = dwc_rd(DEPCMD(ep));

		if (!(v & DEPCMD_CMDACT)) {
			uint32_t status = (v >> 12) & 0xf;

			if (status) {
				usb_dbg[4] = ep * 16u + (cmd & 0xf);
				usb_dbg[5] = status;
				return -1;
			}
			return 0;
		}
		usb_delay_ms(1);
	}
	usb_dbg[4] = 0xf00u + ep * 16u + (cmd & 0xf);
	usb_dbg[5] = 0xdead;
	return -1;
}

static void start_trb(unsigned ep, uint32_t trb_off, uint32_t data_off, uint32_t len, uint32_t ctl)
{
	volatile uint32_t *t = (volatile uint32_t *)(USB_DMA_PA + trb_off);
	uint64_t bpa = USB_DMA_PA + data_off;
	uint64_t tpa = USB_DMA_PA + trb_off;

	t[0] = (uint32_t)bpa;
	t[1] = (uint32_t)(bpa >> 32);
	t[2] = len;
	t[3] = ctl | TRB_HWO | TRB_LST | TRB_ISP_IMI | TRB_IOC;
	__asm__ volatile("dsb sy" ::: "memory");
	epcmd(ep, DEPCMD_STARTTRANSFER, (uint32_t)(tpa >> 32), (uint32_t)tpa, 0);
}

static void start_setup(void)
{
	u.state = EP0_SETUP;
	start_trb(0, UD_TRB_OUT, UD_SETUP, 8, TRBCTL_SETUP);
}

static void ep0_stall(void)
{
	epcmd(0, DEPCMD_SETSTALL, 0, 0, 0);
}

static void handle_setup(void)
{
	const uint8_t *st = (const uint8_t *)(USB_DMA_PA + UD_SETUP);
	uint8_t *data = (uint8_t *)(USB_DMA_PA + UD_DATA);
	uint8_t type = st[0], req = st[1];
	uint16_t value = (uint16_t)(st[2] | (st[3] << 8));
	uint16_t length = (uint16_t)(st[6] | (st[7] << 8));
	uint32_t n = 0;
	int ok = 1;
	int data_in = (type & 0x80) != 0;

	if ((type & 0x60) != 0) {
		ok = 0;
	} else if (req == 6) {			/* GET_DESCRIPTOR */
		switch (value >> 8) {
		case 1:
			for (unsigned i = 0; i < sizeof(dev_desc); i++)
				data[i] = dev_desc[i];
			n = sizeof(dev_desc);
			break;
		case 2:
			for (unsigned i = 0; i < sizeof(cfg_desc); i++)
				data[i] = cfg_desc[i];
			n = sizeof(cfg_desc);
			break;
		case 3:
			n = usb_string(value & 0xff, data);
			ok = n != 0;
			break;
		default:
			ok = 0;
			break;
		}
	} else if (req == 5) {			/* SET_ADDRESS */
		uint32_t dcfg = dwc_rd(DCFG) & ~(0x7fu << 3);

		dwc_wr(DCFG, dcfg | ((uint32_t)(value & 0x7f) << 3));
	} else if (req == 9) {			/* SET_CONFIGURATION */
		u.configured = (value != 0);
	} else if (req == 8) {			/* GET_CONFIGURATION */
		data[0] = u.configured ? 1 : 0;
		n = 1;
	} else if (req == 0) {			/* GET_STATUS */
		data[0] = 0;
		data[1] = 0;
		n = 2;
	} else if (req == 11) {			/* SET_INTERFACE */
	} else {
		ok = 0;
	}

	if (!ok) {
		ep0_stall();
		return;
	}
	if (length == 0) {
		u.state = EP0_WAIT_STATUS2;
		return;
	}
	u.state = EP0_DATA;
	if (data_in) {
		if (n > length)
			n = length;
		start_trb(1, UD_TRB_IN0, UD_DATA, n, 0x50);	/* TRBCTL_DATA */
	} else {
		start_trb(0, UD_TRB_OUT, UD_DATA, 64, 0x50);
	}
}

static void handle_event(uint32_t ev)
{
	if (ev & 1) {				/* device event */
		uint32_t type = (ev >> 8) & 0xf;

		if (type == 1) {		/* USB reset */
			dwc_wr(DCFG, dwc_rd(DCFG) & ~(0x7fu << 3));
			u.configured = 0;
		} else if (type == 2) {		/* connect done */
			/* ep0 max packet 64 at high speed (modify action) */
			epcmd(0, DEPCMD_SETEPCONFIG, (0u << 1) | (64u << 3) | (2u << 30),
			      (1u << 8) | (1u << 10) | (0u << 25), 0);
			epcmd(BULK_DEPCMD_EP, DEPCMD_SETEPCONFIG,
			      (0u << 1) | (64u << 3) | (2u << 30),
			      (1u << 8) | (1u << 10) | (1u << 25), 0);
		} else if (type == 0) {		/* disconnect */
			u.configured = 0;
		}
		return;
	}

	unsigned ep = (ev >> 1) & 0x1f;
	unsigned type = (ev >> 6) & 0xf;
	unsigned status = (ev >> 12) & 0xf;

	if ((int)ep == BULK_EVT_EP) {		/* our bulk IN endpoint */
		if (type == 1) {		/* XferComplete */
			u.bulk_busy = 0;
			u.tx_inflight = 0;
		}
		return;
	}
	if (ep > 1)
		return;

	if (type == 1) {			/* XferComplete */
		switch (u.state) {
		case EP0_SETUP:
			handle_setup();
			break;
		case EP0_DATA:
			u.state = EP0_WAIT_STATUS3;
			break;
		case EP0_STATUS:
			start_setup();
			break;
		default:
			break;
		}
		return;
	}
	if (type == 3 && status == 2) {		/* XferNotReady: control status phase */
		if (u.state == EP0_WAIT_STATUS2 || u.state == EP0_WAIT_STATUS3) {
			uint32_t ctl = (u.state == EP0_WAIT_STATUS3) ? TRBCTL_STATUS3 : TRBCTL_STATUS2;

			u.state = EP0_STATUS;
			start_trb(ep, ep == 1 ? UD_TRB_IN0 : UD_TRB_OUT,
				  UD_DATA, 0, ctl);
		}
	}
}

void usb_poll(void)
{
	uint32_t count = dwc_rd(GEVNTCOUNT) & 0xffff;

	if (count) {
		uint32_t left = count;

		while (left >= 4) {
			uint32_t ev = rd32(USB_DMA_PA + UD_EVT + u.evt_pos);

			u.evt_pos = (u.evt_pos + 4) % UD_EVT_SIZE;
			u.events++;
			handle_event(ev);
			left -= 4;
		}
		dwc_wr(GEVNTCOUNT, count);	/* write-to-clear consumed bytes */
	}

	/* Flush one chunk: copy it into the staging area first, so the queue can
	 * be compacted immediately without touching memory the controller is
	 * still reading. */
	if (u.configured && !u.bulk_busy && u.tx_len) {
		unsigned n = u.tx_len > UD_TX_ACT_SIZE ? UD_TX_ACT_SIZE : u.tx_len;
		uint8_t *q = (uint8_t *)(USB_DMA_PA + UD_TX);
		uint8_t *act = (uint8_t *)(USB_DMA_PA + UD_TX_ACT);

		for (unsigned i = 0; i < n; i++)
			act[i] = q[i];
		for (unsigned i = n; i < u.tx_len; i++)
			q[i - n] = q[i];
		u.tx_len -= n;
		__asm__ volatile("dsb sy" ::: "memory");
		start_trb(BULK_DEPCMD_EP, UD_TRB_BULK, UD_TX_ACT, n, 0);
		u.bulk_busy = 1;
		u.tx_inflight = n;
	}
}

int usb_log_ready(void)
{
	return u.configured;
}

int usb_log_wait_ready(unsigned ms)
{
	uint64_t f = usb_freq();
	uint64_t e = usb_now() + f / 1000u * ms;

	while (usb_now() < e) {
		usb_poll();
		if (u.configured)
			return 1;
		usb_delay_ms(1);
	}
	return u.configured;
}

unsigned usb_log_write(const char *s, unsigned len)
{
	uint8_t *tx = (uint8_t *)(USB_DMA_PA + UD_TX);
	unsigned room = UD_TX_SIZE - u.tx_len;
	unsigned n = len < room ? len : room;

	for (unsigned i = 0; i < n; i++)
		tx[u.tx_len + i] = (uint8_t)s[i];
	u.tx_len += n;
	return n;
}

unsigned usb_log_puts(const char *s)
{
	unsigned n = 0;

	while (s[n])
		n++;
	return usb_log_write(s, n);
}

void usb_log_hold(unsigned ms)
{
	uint64_t f = usb_freq();
	uint64_t e = usb_now() + f / 1000u * ms;

	while (usb_now() < e) {
		usb_poll();
		usb_delay_ms(1);
	}
}

/*
 * READ-ONLY check of the USB power domain and clocks.
 *
 * Touching these was a mistake: writing the GDSC/clock registers can power
 * cycle a block that ABL left perfectly alive (fastboot was running on it a
 * moment ago), and then any DWC3 register access stalls the AXI bus - the CPU
 * hangs with no exception, which is exactly how the first attempt died.
 * So we only read, and rely on what ABL left us.
 *
 * returns 0 when power + clocks look live, negative otherwise:
 *   -1 GDSC not powered
 *   -2 a branch clock reads as gated
 */
/* ---------------------------------------------------------------------------
 * eUSB2 PHY (0x088e3000) initialisation, transcribed from the mainline driver
 * drivers/phy/qualcomm/phy-qcom-snps-eusb2.c (the one this device's kernel is
 * built from): every offset, mask and value below is from that source.
 * This is the piece that was missing: after ABL hands off, the PHY is held in
 * reset (all its registers read 0), so no amount of DWC3 programming can make
 * the device enumerate.
 * ------------------------------------------------------------------------- */
#define PHY_BASE		0x088e3000UL
#define GCC_QUSB2PHY_PRIM_BCR	(GCC_BASE + 0x22000)
#define GCC_QUSB2PHY_SEC_BCR	(GCC_BASE + 0x22004)
#define GCC_USB_PHY_CFG_AHB2PHY	(GCC_BASE + 0x7a000)

#define PHY_UTMI_CTRL0		0x03c
#define PHY_UTMI_CTRL5		0x050
#define PHY_HS_PHY_CTRL_COMMON0	0x054
#define PHY_CFG_CTRL_1		0x058
#define PHY_CFG_CTRL_2		0x05c
#define PHY_CFG_CTRL_3		0x060
#define PHY_HS_PHY_CTRL2	0x064
#define PHY_CFG_CTRL_4		0x068
#define PHY_CFG_CTRL_5		0x06c
#define PHY_CFG_CTRL_6		0x070
#define PHY_CFG_CTRL_8		0x078
#define PHY_CFG_CTRL_9		0x07c
#define PHY_CFG0		0x094
#define PHY_UTMI_PHY_CMN_CTRL0	0x098
#define PHY_FSEL_SEL		0x0b8
#define PHY_APB_ACCESS_CMD	0x130

#define MASK(h, l)		(((1u << ((h) - (l) + 1)) - 1u) << (l))

static uint32_t phy_rd(uint32_t off)
{
	return rd32(PHY_BASE + off);
}

static void phy_wr(uint32_t off, uint32_t v)
{
	wr32(PHY_BASE + off, v);
	__asm__ volatile("dsb sy" ::: "memory");
}

/* write_mask() from the driver: (old & ~mask) | (val & mask) */
static void phy_mask(uint32_t off, uint32_t mask, uint32_t val)
{
	phy_wr(off, (phy_rd(off) & ~mask) | (val & mask));
}

/* 0 = the PHY answered (revision register non-zero), negative = it is dead */
static int usb_phy_alive(void)
{
	uint32_t rev = phy_rd(0x0);

	return (rev == 0 || rev == 0xffffffffu) ? -1 : 0;
}

int usb_phy_init(void)
{
	/* Reference clocks; only touch a branch that actually reads as gated. */
	static const uint32_t clk[] = {
		GCC_BASE + 0x4905c,	/* phy_aux */
		GCC_BASE + 0x49060,	/* phy_com_aux */
		GCC_BASE + 0x49064,	/* phy_pipe */
		GCC_BASE + 0x49018,	/* master */
	};
	for (unsigned i = 0; i < sizeof(clk) / sizeof(clk[0]); i++) {
		if (rd32(clk[i]) & (1u << 31))
			wr32(clk[i], rd32(clk[i]) | 1u);
	}

	/* Release the PHY resets (BCR bit0 = asserted). */
	wr32(GCC_QUSB2PHY_PRIM_BCR, 0);
	wr32(GCC_QUSB2PHY_SEC_BCR, 0);
	usb_delay_ms(2);
	if (usb_phy_alive())
		return -1;

	/* 19.2 MHz reference -> FSEL 0, PLL FB divider 0x190 */
	phy_mask(PHY_CFG0, MASK(1, 1), MASK(1, 1));		/* CMN_CTRL_OVERRIDE_EN */
	phy_mask(PHY_UTMI_CTRL5, (1u << 1), (1u << 1));		/* POR */
	phy_mask(PHY_HS_PHY_CTRL_COMMON0, (1u << 0) | (1u << 3),
		 (1u << 0) | (1u << 3));			/* PHY_ENABLE|RETENABLEN */
	phy_mask(PHY_APB_ACCESS_CMD, (1u << 2), (1u << 2));	/* APB_LOGIC_RESET */
	phy_mask(PHY_UTMI_PHY_CMN_CTRL0, (1u << 6), 0);		/* TESTBURNIN off */
	phy_mask(PHY_FSEL_SEL, 1u, 1u);
	/* qcom_eusb2_ref_clk_init() for a 19.2 MHz clock */
	phy_mask(PHY_HS_PHY_CTRL_COMMON0, MASK(6, 4), 0x0);	/* FSEL 19.2 */
	phy_mask(PHY_CFG_CTRL_2, MASK(7, 0), 0x90);
	phy_mask(PHY_CFG_CTRL_3, MASK(3, 0), 0x1);
	phy_mask(PHY_CFG_CTRL_3, MASK(7, 4), 0x0);		/* PLL_REF_DIV */
	/* PLL tuning */
	phy_mask(PHY_CFG_CTRL_1, MASK(7, 1), 0x1 << 1);
	phy_mask(PHY_CFG_CTRL_4, MASK(7, 2), 0x8 << 2);
	phy_mask(PHY_CFG_CTRL_4, MASK(1, 0), 0x1);
	phy_mask(PHY_CFG_CTRL_5, MASK(4, 0), 0x10);
	phy_mask(PHY_CFG_CTRL_6, MASK(2, 0), 0x0);
	phy_mask(PHY_CFG_CTRL_5, MASK(7, 6), 0x1 << 6);
	phy_mask(PHY_HS_PHY_CTRL2, (1u << 4), (1u << 4));	/* VBUS_DET_EXT_SEL */
	/* qcom_eusb2_default_parameters() */
	phy_mask(PHY_CFG_CTRL_9, MASK(2, 0), 0x0);		/* tx pre-emphasis */
	phy_mask(PHY_CFG_CTRL_9, MASK(6, 5), 0x2 << 5);		/* tx rise/fall */
	phy_mask(PHY_CFG_CTRL_9, MASK(4, 3), 0x1 << 3);		/* tx res */
	phy_mask(PHY_CFG_CTRL_8, MASK(5, 3), 0x3 << 3);		/* hs vref */
	phy_mask(PHY_CFG_CTRL_8, MASK(7, 6), 0x0);		/* hs xv */
	/* power-up tail */
	phy_mask(PHY_HS_PHY_CTRL2, (1u << 3) | (1u << 2), (1u << 3) | (1u << 2));
	phy_mask(PHY_UTMI_CTRL0, 1u, 1u);			/* SLEEPM */
	phy_mask(PHY_HS_PHY_CTRL_COMMON0, (1u << 1), (1u << 1));	/* SIDDQ_SEL */
	phy_mask(PHY_HS_PHY_CTRL_COMMON0, (1u << 2), 0);	/* SIDDQ off */
	phy_mask(PHY_UTMI_CTRL5, (1u << 1), 0);			/* POR off */
	phy_mask(PHY_HS_PHY_CTRL2, (1u << 3), 0);		/* SUSPEND_N_SEL off */
	usb_delay_ms(5);
	return usb_phy_alive();
}

/* Small read-only snapshot for the screen report. */
void usb_phy_snapshot(uint32_t *out)
{
	out[0] = phy_rd(0x0);
	out[1] = phy_rd(0x4);
	out[2] = phy_rd(PHY_HS_PHY_CTRL_COMMON0);
	out[3] = phy_rd(PHY_HS_PHY_CTRL2);
	out[4] = phy_rd(PHY_UTMI_CTRL5);
	out[5] = rd32(GCC_QUSB2PHY_PRIM_BCR);
	out[6] = rd32(GCC_QUSB2PHY_SEC_BCR);
	out[7] = rd32(GCC_BASE + 0x4905c);
}

static int gcc_usb_probe(void)
{
	uint32_t gdsc = rd32(GCC_USB30_PRIM_GDSC);
	uint32_t sts = 0;

	if (!(gdsc & GDSC_PWR_STS))
		return -1;

	static const uint32_t clk[] = {
		GCC_USB30_PRIM_MASTER, GCC_USB30_PRIM_SLEEP, GCC_USB30_PRIM_UTMI,
		GCC_USB30_PRIM_CFGNOC, GCC_USB30_PRIM_AGGRE,
	};
	for (unsigned i = 0; i < sizeof(clk) / sizeof(clk[0]); i++) {
		uint32_t v = rd32(clk[i]);

		sts |= v & (1u << 31);		/* CLK_OFF still set */
		if (!(v & 1u))
			sts |= 1u;		/* enable bit clear */
	}
	return sts ? -2 : 0;
}

int usb_log_init(void)
{
	int rc;

	u.evt_pos = 0;
	u.events = 0;
	u.state = EP0_SETUP;
	u.configured = 0;
	u.bulk_busy = 0;
	u.tx_len = 0;
	u.tx_inflight = 0;

	rc = gcc_usb_probe();
	if (rc)
		return rc;

	/*
	 * PHY soft reset first, as the working DWC3 device driver does:
	 * GUSB2PHYCFG.PHYSOFTRST, wait, release. Without this the wrapper keeps the
	 * PHY in whatever state the bootloader left and no session is ever seen.
	 */
	{
		uint32_t cfg = dwc_rd(GUSB2PHYCFG(0));

		dwc_wr(GUSB2PHYCFG(0), cfg | GUSB2PHYCFG_PHYSOFTRST);
		__asm__ volatile("dsb sy" ::: "memory");
		for (volatile uint32_t i = 0; i < 200000u; i++) {
		}
		dwc_wr(GUSB2PHYCFG(0), cfg & ~GUSB2PHYCFG_PHYSOFTRST);
		__asm__ volatile("dsb sy" ::: "memory");
		for (volatile uint32_t i = 0; i < 200000u; i++) {
		}
		usb_dbg[4] = dwc_rd(GUSB2PHYCFG(0));
	}

	/* Device controller soft reset; PHY and wrapper stay as ABL left them. */
	usb_dbg[0] = dwc_rd(GSNPSID);
	usb_dbg[1] = dwc_rd(GCTL);
	usb_dbg[2] = dwc_rd(DSTS);
	usb_dbg[3] = dwc_rd(DCFG);
	/*
	 * GSNPSID normally reads 0x55xxxxxx on a DWC3. On this K60 it reads
	 * 0x33313130 ("0113" in ASCII) while GCTL/DSTS/DCFG look sane, so treat the
	 * ID as a warning: bailing here is why the USB log channel never started.
	 */
	if ((usb_dbg[0] >> 24) != 0x55) {
		usb_dbg[7] = 0xbad1d;
	}

#ifdef USB_NO_GCTL_WRITE
	/* Keep the controller exactly as ABL left it. Every variant that wrote
	 * GCTL (host-mode mistake and even the databook CORESOFTRESET sequence)
	 * wedged the AXI bus, while the read-only + DCTL-reset path did not, so
	 * the GCTL write is off the table until we know why. */
	usb_dbg[0] = dwc_rd(GSNPSID);
	usb_dbg[1] = dwc_rd(GCTL);
	usb_dbg[2] = dwc_rd(DSTS);
	usb_dbg[3] = dwc_rd(DCFG);
	/*
	 * GSNPSID normally reads 0x55xxxxxx on a DWC3. On this K60 it reads
	 * 0x33313130 ("0113" in ASCII) while GCTL/DSTS/DCFG look sane, so treat the
	 * ID as a warning: bailing here is why the USB log channel never started.
	 */
	if ((usb_dbg[0] >> 24) != 0x55) {
		usb_dbg[7] = 0xbad1d;
	}

	dwc_wr(DCTL, DCTL_CSFTRST);
	for (int i = 0; i < 500 && (dwc_rd(DCTL) & DCTL_CSFTRST); i++)
		usb_delay_ms(1);
	if (dwc_rd(DCTL) & DCTL_CSFTRST)
		return -3;
#else
	/* Databook sequence (what Linux dwc3 does in dwc3_core_soft_reset):
	 *   1. GCTL.CORESOFTRESET (bit 11) = 1, wait until the core clears it
	 *   2. GCTL.PrtCapDir (bits 13:12) = DEVICE
	 * DEVICE is 2, not 1 - writing 1 selects HOST mode, which is what wedged
	 * the controller the last time (every device endpoint command then fails
	 * and the following register accesses stall the bus). */
	dwc_wr(GCTL, dwc_rd(GCTL) | GCTL_CORESOFTRESET);
	for (int i = 0; i < 1000 && (dwc_rd(GCTL) & GCTL_CORESOFTRESET); i++)
		usb_delay_ms(1);
	if (dwc_rd(GCTL) & GCTL_CORESOFTRESET)
		return -5;
	dwc_wr(GCTL, (dwc_rd(GCTL) & ~(3u << GCTL_PRTCAP_SHIFT)) |
		     (GCTL_PRTCAP_DEVICE << GCTL_PRTCAP_SHIFT));
	usb_delay_ms(2);
	if (((dwc_rd(GCTL) >> GCTL_PRTCAP_SHIFT) & 3u) != GCTL_PRTCAP_DEVICE)
		return -6;

	dwc_wr(DCTL, DCTL_CSFTRST);
	for (int i = 0; i < 500 && (dwc_rd(DCTL) & DCTL_CSFTRST); i++)
		usb_delay_ms(1);
	if (dwc_rd(DCTL) & DCTL_CSFTRST)
		return -3;
#endif

	dwc_wr(GEVNTADRLO, (uint32_t)(USB_DMA_PA + UD_EVT));
	dwc_wr(GEVNTADRHI, (uint32_t)((USB_DMA_PA + UD_EVT) >> 32));
	dwc_wr(GEVNTSIZ, (1u << 31) | UD_EVT_SIZE);	/* interrupt masked: polled */
	dwc_wr(GEVNTCOUNT, dwc_rd(GEVNTCOUNT) & 0xffff);

	/* High speed, address 0 */
	dwc_wr(DCFG, dwc_rd(DCFG) & ~(7u | (0x7fu << 3)));
	dwc_wr(DEVTEN, 0x7);			/* disconnect, reset, connect done */

	/* ep0 (both directions) and ep1... our bulk IN lives in the DEPCMD slot
	 * matching its endpoint number, which is 1 for 0x81. */
	if (epcmd(0, DEPCMD_DEPSTARTCFG, 0, 0, 0))
		return -100;
	if (epcmd(0, DEPCMD_SETEPCONFIG, (0u << 1) | (512u << 3),
		  (1u << 8) | (1u << 10) | (0u << 25), 0))
		return -101;
	if (epcmd(0, DEPCMD_SETTRANSFRESOURCE, 1, 0, 0))
		return -102;
	if (epcmd(BULK_DEPCMD_EP, DEPCMD_SETEPCONFIG, (0u << 1) | (512u << 3),
		  (1u << 8) | (1u << 10) | (1u << 25), 0))
		return -103;
	if (epcmd(BULK_DEPCMD_EP, DEPCMD_SETTRANSFRESOURCE, 1, 0, 0))
		return -104;

	dwc_wr(DALEPENA, DALEPENA_EP0_AND_BULK);
	start_setup();

	/*
	 * VBUS session override. The device-mode driver used on this SoC family
	 * forces VBUS/BVALID valid and selects the external VBUS detector; on the
	 * Qualcomm wrapper those are QSCRATCH_GENERAL_CFG bits 6 (VBUSVLDEXT_SEL)
	 * and 7 (VBUSVLDEXT), next to the HS/SS PHY control bits below.
	 */
	{
		uint32_t gen = rd32(QSCRATCH_BASE + 0x08);
		uint32_t hs = rd32(QSCRATCH_BASE + 0x10);
		uint32_t ss = rd32(QSCRATCH_BASE + 0x30);

		wr32(QSCRATCH_BASE + 0x08, gen | (1u << 6) | (1u << 7));
		wr32(QSCRATCH_BASE + 0x10, hs | (1u << 20) | (1u << 28));
		wr32(QSCRATCH_BASE + 0x30, ss | (1u << 24));
		__asm__ volatile("dsb sy" ::: "memory");
		usb_dbg[5] = rd32(QSCRATCH_BASE + 0x08);
		usb_dbg[6] = rd32(QSCRATCH_BASE + 0x10);
	}

	dwc_wr(DCTL, dwc_rd(DCTL) | DCTL_RUN_STOP);
	for (int i = 0; i < 500 && (dwc_rd(DSTS) & DSTS_DEVCTRLHLT); i++)
		usb_delay_ms(1);
	if (dwc_rd(DSTS) & DSTS_DEVCTRLHLT)
		return -5;
	return 0;
}
