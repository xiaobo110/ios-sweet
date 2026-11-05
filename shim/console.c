/*
 * Minimal text console on the ABL splash framebuffer. With the MMU off
 * every access is Device memory, so stay aligned (-mstrict-align).
 */
#include "console.h"
#include "font.h"

#define SCALE		3
#define CH_W		(8 * SCALE)
#define CH_H		(16 * SCALE)
#define MARGIN		24
#define LABEL_COLS	10
#define STATUS_COLS	72
#define COL_MAX		110

volatile uint32_t *con_fb = (volatile uint32_t *)FB_BASE;
static uint32_t con_bg;
static int row;

void con_clear(uint32_t bg)
{
	for (int y = 0; y < FB_H; y++) {
		volatile uint32_t *line = con_fb + y * (FB_STRIDE / 4);
		for (int x = 0; x < FB_W; x++)
			line[x] = bg;
	}
	con_bg = bg;
	row = 0;
}

static void put_char(int col, char ch, uint32_t fg)
{
	if (ch < 0x20 || ch > 0x7e)
		ch = '?';
	const unsigned char *g = font8x16[ch - 0x20];
	int x0 = MARGIN + col * CH_W, y0 = MARGIN + row * CH_H;
	if (x0 + CH_W > FB_W || y0 + CH_H > FB_H)
		return;
	for (int gy = 0; gy < 16; gy++)
		for (int gx = 0; gx < 8; gx++) {
			uint32_t c = (g[gy] & (0x80 >> gx)) ? fg : con_bg;
			for (int sy = 0; sy < SCALE; sy++) {
				volatile uint32_t *p = con_fb + (y0 + gy * SCALE + sy) * (FB_STRIDE / 4)
						       + x0 + gx * SCALE;
				for (int sx = 0; sx < SCALE; sx++)
					p[sx] = c;
			}
		}
}

static int put_str(int col, const char *s, uint32_t fg)
{
	while (*s)
		put_char(col++, *s++, fg);
	return col;
}

static void fmt_hex(char *buf, uint64_t v, int digits)
{
	static const char hex[] = "0123456789ABCDEF";
	for (int i = 0; i < digits; i++)
		buf[i] = hex[(v >> (4 * (digits - 1 - i))) & 0xf];
	buf[digits] = 0;
}

void con_banner(const char *text, const char *sub, uint32_t fg, uint32_t bg, int scale)
{
	int n = 0;
	for (const char *p = text; *p; p++)
		n++;
	int w = n * 8 * scale, h = 16 * scale;
	int x0 = (FB_W - w) / 2, y0 = (FB_H - h) / 3;

	for (int y = 0; y < h + 8 * scale; y++) {	/* also clear a strip below */
		volatile uint32_t *line = con_fb + (y0 + y) * (FB_STRIDE / 4) + x0;
		for (int x = 0; x < w; x++)
			line[x] = bg;
	}
	for (int i = 0; i < n; i++) {
		char ch = text[i];
		if (ch < 0x20 || ch > 0x7e)
			ch = '?';
		const unsigned char *g = font8x16[ch - 0x20];
		int cx = x0 + i * 8 * scale;
		for (int gy = 0; gy < 16; gy++)
			for (int gx = 0; gx < 8; gx++) {
				if (!(g[gy] & (0x80 >> gx)))
					continue;
				for (int sy = 0; sy < scale; sy++) {
					volatile uint32_t *rowp = con_fb +
					    (y0 + gy * scale + sy) * (FB_STRIDE / 4) + cx + gx * scale;
					for (int sx = 0; sx < scale; sx++)
						rowp[sx] = fg;
				}
			}
	}
	con_bg = bg;
	row = (y0 + h) / CH_H + 1;
	if (sub)
		put_str(0, sub, fg);
	row++;
}


#ifdef K60_CONSOLE_USB
#include "usb.h"

/* Mirror the on-screen console into the USB log channel, so a probe's whole
 * diagnostic output reaches the host as text instead of being hidden behind a
 * panel we cannot drive yet. */
static void con_mirror(const char *label, const char *a, uint64_t va,
		       const char *b, uint64_t vb)
{
	char line[96];
	unsigned n = 0;
	char hex[17];

	if (!usb_log_ready())
		return;
	for (const char *q = label; q && *q && n < 24; q++)
		line[n++] = *q;
	line[n++] = ' ';
	if (a) {
		for (const char *q = a; q && *q && n < 48; q++)
			line[n++] = *q;
		line[n++] = '=';
		fmt_hex(hex, va, 8);
		for (int i = 0; i < 8; i++)
			line[n++] = hex[i];
	}
	if (b) {
		line[n++] = ' ';
		for (const char *q = b; q && *q && n < 78; q++)
			line[n++] = *q;
		line[n++] = '=';
		fmt_hex(hex, vb, 8);
		for (int i = 0; i < 8; i++)
			line[n++] = hex[i];
	}
	line[n++] = '\n';
	usb_log_write(line, n);
	usb_poll();
}
#endif

void con_skip(void)
{
	row++;
}

void con_str(const char *label, const char *s)
{
#ifdef K60_CONSOLE_USB
	{
		char line[96];
		unsigned n = 0;

		if (usb_log_ready()) {
			for (const char *q = label; q && *q && n < 24; q++)
				line[n++] = *q;
			line[n++] = ' ';
			for (const char *q = s; q && *q && n < 92; q++)
				line[n++] = *q;
			line[n++] = '\n';
			usb_log_write(line, n);
			usb_poll();
		}
	}
#endif
	put_str(0, label, YELLOW);
	put_str(LABEL_COLS, s, WHITE);
	row++;
}

/* ---- fastfetch-ish layout helpers -------------------------------------- */
/* One horizontal rule made of ASCII (the font only covers 0x20-0x7e). */

/* ---- scaled output (readable in a phone photo) ------------------------- */
static int big_y;

void con_big_reset(void)
{
	big_y = MARGIN;
}

/* Draw one line with an integer scale, wrapping at the screen edge. */
void con_big(const char *text, uint32_t fg, int scale)
{
	int x = MARGIN;
	int line_h = 16 * scale;

	for (const char *p = text; *p; p++) {
		char ch = (*p < 0x20 || *p > 0x7e) ? '?' : *p;
		const unsigned char *g = font8x16[ch - 0x20];

		if (x + 8 * scale > FB_W) {
			x = MARGIN;
			big_y += line_h;
		}
		if (big_y + line_h > FB_H)
			return;
		for (int gy = 0; gy < 16; gy++) {
			for (int gx = 0; gx < 8; gx++) {
				uint32_t col = (g[gy] & (0x80 >> gx)) ? fg : 0xff101420u;

				for (int sy = 0; sy < scale; sy++) {
					volatile uint32_t *pp = con_fb +
						(big_y + gy * scale + sy) * (FB_STRIDE / 4) +
						x + gx * scale;
					for (int sx = 0; sx < scale; sx++)
						pp[sx] = col;
				}
			}
		}
		x += 8 * scale;
	}
	big_y += line_h + 6 * scale;
}

void con_rule(char ch)
{
	char line[FB_W / 8 + 1];
	int n = 0;

	while (n < COL_MAX - 1) {
		line[n++] = ch;
	}
	line[n] = 0;
	put_str(0, line, 0xff5f87d7U);
	row++;
}

/* label (yellow)  value (white)  status (green/yellow/red at a fixed column) */
void con_kv(const char *label, const char *value, const char *status, int level)
{
	int col = put_str(0, label, YELLOW);

	col = col < LABEL_COLS ? LABEL_COLS : col + 1;
	col = put_str(col, value, WHITE);
	int scol = STATUS_COLS;
	put_str(scol, "[ ", 0xff5f87d7U);
	put_str(scol + 2, status,
		level > 0 ? GREEN : level < 0 ? 0xffff5555U : YELLOW);
	row++;
}

/* Section heading: a rule, the title, then a dimmer rule. */
void con_section(const char *title)
{
	con_rule('-');
	put_str(0, title, 0xff5f87d7U);
	row++;
	con_rule('-');
}

void con_hex(const char *label, uint64_t v)
{
	char buf[17];
	fmt_hex(buf, v, 16);
	con_str(label, buf);
}

void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb)
{
	char buf[9];
#ifdef K60_CONSOLE_USB
	con_mirror(label, a, va, b, vb);
#endif
	int col = put_str(0, label, YELLOW);
	col = col < LABEL_COLS ? LABEL_COLS : col + 1;
	col = put_str(col, a, YELLOW);
	fmt_hex(buf, va, 8);
	col = put_str(col, buf, WHITE);
	if (b) {
		col = put_str(col + 1, b, YELLOW);
		fmt_hex(buf, vb, 8);
		put_str(col, buf, WHITE);
	}
	row++;
}
