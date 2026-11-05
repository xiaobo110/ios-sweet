/*
 * Milestone 4: act as iBoot for xnu.
 *
 * mkpayload.py already resolved the kernel's chained fixups and built the
 * ADT, so this only has to: find the payload in the ABL ramdisk, copy the
 * segments to phys_base + (vmaddr - virt_base), place the ADT and
 * boot_args behind the kernel, and jump to the entry point with the MMU
 * and caches off, x0 = boot_args (physical).
 */
#include <stdint.h>
#include "console.h"
#include "fdt.h"
#include "loader.h"

#define BOOT_LINE_LENGTH	608
#define PAYLOAD_SAFE_PA	0x0f8000000UL	/* bank 2, above the kernel range */
#define PAGE_16K		0x4000UL

struct payload_seg {
	uint64_t vmaddr, vmsize, file_off, file_size;
};

struct payload {
	char magic[8];
	uint32_t version, nsegs;
	uint64_t virt_base, entry_va, adt_off, adt_size, kernel_end;
	uint64_t phys_base, mem_size;
	char cmdline[BOOT_LINE_LENGTH];
	struct payload_seg segs[];
};

/* pexpert/pexpert/arm64/boot.h, Revision/Version 2 */
struct boot_video {
	uint64_t v_baseAddr, v_display, v_rowBytes, v_width, v_height, v_depth;
};

struct boot_args {
	uint16_t Revision, Version;
	uint64_t virtBase, physBase, memSize, topOfKernelData;
	struct boot_video Video;
	uint32_t machineType;
	uint64_t deviceTreeP;
	uint32_t deviceTreeLength;
	char CommandLine[BOOT_LINE_LENGTH];
	uint64_t bootFlags, memSizeActual;
};

_Static_assert(__builtin_offsetof(struct payload, segs) == 0x2a8, "payload header layout");
_Static_assert(__builtin_offsetof(struct boot_args, Video) == 40, "boot_args layout");
_Static_assert(__builtin_offsetof(struct boot_args, CommandLine) == 108, "boot_args layout");
_Static_assert(sizeof(struct boot_args) == 736, "boot_args layout");

/* MMU off: everything is Device memory, so copy in aligned 8-byte words. */
static void copy64(uintptr_t dst, uintptr_t src, uint64_t n)
{
	volatile uint64_t *d = (volatile uint64_t *)dst;
	volatile const uint64_t *s = (volatile const uint64_t *)src;
	for (uint64_t i = 0; i < (n + 7) / 8; i++)
		d[i] = s[i];
}

/* Always 16 digits: the token below has to be greppable at a fixed width. */
static char *put_hex64(char *o, uint64_t v)
{
	static const char hx[] = "0123456789abcdef";

	*o++ = '0';
	*o++ = 'x';
	for (int sh = 60; sh >= 0; sh -= 4)
		*o++ = hx[(v >> sh) & 0xf];
	return o;
}

static void zero64(uintptr_t dst, uint64_t n)
{
	volatile uint64_t *d = (volatile uint64_t *)dst;
	for (uint64_t i = 0; i < n / 8; i++)
		d[i] = 0;
}

static int magic_ok(const char *m)
{
	static const char want[] = "SWEETXNU";
	for (int i = 0; i < 8; i++)
		if (m[i] != want[i])
			return 0;
	return 1;
}

typedef void (*xnu_entry_t)(uint64_t boot_args_pa);

const char *loader_boot(uintptr_t dtb)
{
	uint64_t rd_start, rd_end;

	if (!fdt_chosen_u64(dtb, "linux,initrd-start", &rd_start) ||
	    !fdt_chosen_u64(dtb, "linux,initrd-end", &rd_end))
		return "no initrd in /chosen";
	con_hex2("INITRD", "lo=", rd_start, "hi=", rd_end);

	/* Preferred delivery on K60: the payload is appended to our own kernel
	 * blob, right after the shim image, because this ABL does not pass the
	 * boot.img ramdisk through. */
	extern char _end[];
	{
		uintptr_t own = ((uintptr_t)_end + 7) & ~7UL;
		uint64_t limit = 96ULL << 20;
		const struct payload *q = 0;

		for (uint64_t off = 0; off < limit; off += 8) {
			const char *m = (const char *)(own + off);

			if (m[0] == 'S' && m[1] == 'W' && m[2] == 'E' && m[3] == 'E' &&
			    m[4] == 'T' && m[5] == 'X' && m[6] == 'N' && m[7] == 'U') {
				q = (const struct payload *)m;
				con_hex2("PAYLOAD", "self-image at +", (uint32_t)off, "lo", (uint32_t)own);
				break;
			}
		}
		if (q && magic_ok(q->magic) && q->version == 1) {
			/* The payload sits inside the DRAM the kernel itself will
			 * occupy (phys_base + first segments), so copying it there
			 * would overwrite the source mid-way. Move it above the
			 * kernel range first: the MMU is still off here, so a plain
			 * physical copy works. */
			extern void *memcpy(void *, const void *, size_t);
			uint64_t need = 0;
			const uint8_t *src = (const uint8_t *)q;
			uint8_t *dst = (uint8_t *)PAYLOAD_SAFE_PA;

			for (uint32_t i = 0; i < q->nsegs; i++) {
				uint64_t e = q->segs[i].file_off + q->segs[i].file_size;

				if (e > need)
					need = e;
			}
			if (q->adt_off + q->adt_size > need)
				need = q->adt_off + q->adt_size;
			need = (need + 0xfffffUL) & ~0xfffffUL;	/* round up 1 MiB */
			memcpy(dst, src, need);
			__asm__ volatile("dsb sy" ::: "memory");
			con_hex2("MOVED", "bytes=", (uint32_t)need, "to", PAYLOAD_SAFE_PA);
			rd_start = (uintptr_t)dst;
			rd_end = rd_start + need;
		}
	}

	const struct payload *p = (const struct payload *)rd_start;
	if (!magic_ok(p->magic)) {
		/* ABL does not necessarily hand the payload over at offset 0 (it may
		 * decompress into its own buffer or add a prefix), so hunt for the
		 * magic across the whole initrd region. */
		uint64_t span = rd_end > rd_start ? rd_end - rd_start : 0;
		uint64_t off;

		if (span > 0x4000000ULL)
			span = 0x4000000ULL;
		for (off = 8; off + 8 <= span; off += 8) {
			const char *m = (const char *)(rd_start + off);

			if (m[0] == 'S' && m[1] == 'W' && m[2] == 'E' && m[3] == 'E' &&
			    m[4] == 'T' && m[5] == 'X' && m[6] == 'N' && m[7] == 'U') {
				p = (const struct payload *)m;
				con_hex2("PAYLOAD", "found at +", (uint32_t)off, "span", (uint32_t)span);
				break;
			}
		}
		if (!magic_ok(p->magic))
			return "no SWEETXNU payload";
	}
	if (p->version != 1 || (p->adt_off & 7) || (rd_start & 7))
		return "bad payload header";

	uint64_t pa_of_va_base = p->phys_base - p->virt_base;	/* pa = va + this */
	con_hex2("PHYS", "base=", p->phys_base, "size=", p->mem_size);
	con_hex("VIRT", p->virt_base);
	con_hex("ENTRY", p->entry_va);

	/* The payload must not overlap where the kernel goes. */
	if (rd_end > p->phys_base && rd_start < p->phys_base + p->mem_size)
		return "ramdisk overlaps kernel memory";

	for (uint32_t i = 0; i < p->nsegs; i++) {
		const struct payload_seg *s = &p->segs[i];
		uintptr_t dst = s->vmaddr + pa_of_va_base;
		copy64(dst, rd_start + s->file_off, s->file_size);
		if (s->vmsize > s->file_size)
			zero64(dst + ((s->file_size + 7) & ~7UL), s->vmsize - ((s->file_size + 7) & ~7UL));
	}
	con_hex2("SEGS", "n=", p->nsegs, "end=", p->kernel_end + pa_of_va_base);

	/* ADT, then boot_args, behind the kernel; everything below topOfKernelData is ours. */
	uintptr_t adt_pa = p->kernel_end + pa_of_va_base;
	copy64(adt_pa, rd_start + p->adt_off, p->adt_size);
	uintptr_t ba_pa = (adt_pa + p->adt_size + PAGE_16K - 1) & ~(PAGE_16K - 1);
	uintptr_t top = ba_pa + PAGE_16K;

	zero64(ba_pa, PAGE_16K);
	volatile struct boot_args *ba = (volatile struct boot_args *)ba_pa;
	ba->Revision = 2;
	ba->Version = 2;
	ba->virtBase = p->virt_base;
	ba->physBase = p->phys_base;
	/*
	 * SWEET: let the physmap reach the top of the accessible window (4 GiB)
	 * while the VM's managed range stays limited by the ADT's dram-size. That
	 * gap is what lets the kernel hand the extra clean ranges to the VM with
	 * ml_static_mfree() instead of leaving them unusable.
	 */
	{
		uint64_t phys_span = (p->phys_base < 0x100000000ULL) ?
		    (0x100000000ULL - p->phys_base) : p->mem_size;

		ba->memSize = phys_span > p->mem_size ? phys_span : p->mem_size;
		con_hex2("MEMSZ", "managed=", p->mem_size, "physmap", ba->memSize);
	}
	ba->topOfKernelData = top;
	ba->Video.v_baseAddr = FB_BASE;
	ba->Video.v_display = 1;
	ba->Video.v_rowBytes = FB_STRIDE;
	ba->Video.v_width = FB_W;
	ba->Video.v_height = FB_H;
	ba->Video.v_depth = 32;
	con_hex2("TOPK", "adt=", adt_pa, "top", top);
	ba->deviceTreeP = adt_pa - pa_of_va_base;	/* xnu reads it with the MMU on: a VA */
	ba->deviceTreeLength = (uint32_t)p->adt_size;
	for (int i = 0; i < BOOT_LINE_LENGTH; i++)
		ba->CommandLine[i] = p->cmdline[i];
	ba->memSizeActual = p->mem_size;

	/*
	 * SWEET: leave a read-back token in the command line.
	 *
	 * The kernel's early console does not survive: sweet_log_dump() replays the
	 * previous boot's ring over it (bsd/dev/sweet_ufs.c), so "did the shim
	 * really write what we think it did" cannot be answered from the boot
	 * lines. launchd prints CommandLine verbatim as "boot-args = ..." well
	 * after that replay, so a token appended here is the one copy of the shim's
	 * own view that is still readable on the next boot.
	 *
	 * Layout (all hex, order fixed):
	 *   sweetba=<phys_base>:<payload mem_size>:<ba->memSize>:<ba->memSizeActual>
	 *            :<adt_pa>:<topOfKernelData>:<kernel_end_pa>
	 */
	{
		char *o = (char *)(uintptr_t)ba->CommandLine;
		uint32_t n = 0;

		while (n < BOOT_LINE_LENGTH && o[n] != 0)
			n++;
		if (n + 8 + 7 * 18 + 8 < BOOT_LINE_LENGTH) {
			o += n;
			*o++ = ' ';
			const char *k = "sweetba=";
			while (*k)
				*o++ = *k++;
			o = put_hex64(o, p->phys_base);          *o++ = ':';
			o = put_hex64(o, p->mem_size);           *o++ = ':';
			o = put_hex64(o, ba->memSize);           *o++ = ':';
			o = put_hex64(o, ba->memSizeActual);     *o++ = ':';
			o = put_hex64(o, adt_pa);                *o++ = ':';
			o = put_hex64(o, top);                   *o++ = ':';
			o = put_hex64(o, p->kernel_end + pa_of_va_base);
			*o = 0;
		}
	}
	con_hex2("BOOTARG", "pa=", ba_pa, "top=", top);

	uintptr_t entry_pa = p->entry_va + pa_of_va_base;
	con_hex("JUMP", entry_pa);
	con_str("XNU", "jumping (fbcon clears screen)");

	/* Everything was written with the MMU and caches off; drop any stale I-cache lines too. */
	__asm__ volatile("dsb sy; ic iallu; dsb sy; isb" ::: "memory");
	((xnu_entry_t)entry_pa)(ba_pa);
	return "xnu returned";
}
