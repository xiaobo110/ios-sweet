#pragma once
#include <stdint.h>

#define FB_BASE		0xb8000000UL	/* diting: cont_splash_region, 43MB */
#define FB_W		1220
#define FB_H		2712
#define FB_STRIDE	(FB_W * 4)	/* a8r8g8b8, matches mainline simplefb */

#define RED	0xffff0000U
#define BLUE	0xff0000a0U
#define WHITE	0xffffffffU
#define YELLOW	0xffffff00U
#define GREEN	0xff00ff00U

extern volatile uint32_t *con_fb;

void con_clear(uint32_t bg);
void con_skip(void);
/* Draw a large centred banner (ASCII art from the 8x16 font) and leave the
 * text cursor below it. scale is the font pixel multiplier. */
void con_banner(const char *text, const char *sub, uint32_t fg, uint32_t bg, int scale);
void con_str(const char *label, const char *s);
void con_hex(const char *label, uint64_t v);
/* label, then "a=xxxxxxxx b=xxxxxxxx" style pairs of 32-bit values */
void con_hex2(const char *label, const char *a, uint32_t va, const char *b, uint32_t vb);
/* fastfetch-style helpers: rule / section heading / aligned key-value-status row */
void con_rule(char ch);
void con_kv(const char *label, const char *value, const char *status, int level);
void con_section(const char *title);
/* Scaled text, for panic logs that must be legible in a photo. */
void con_big_reset(void);
void con_big(const char *text, uint32_t fg, int scale);
