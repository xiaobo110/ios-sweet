#pragma once
#include <stdint.h>

void test_ufs(void);
int ufs_host_init(void);
int ufs_take_over(void);
int ufs_read_lowblk(uint32_t lba);
extern int ufs_scsi_rc, ufs_scsi_ocs;
extern uint8_t ufs_sense[20];
const uint8_t *ufs_bounce(void);
int ufs_readcap(uint32_t *lastlba, uint32_t *blk);
int ufs_read1(uint32_t lba);
int ufs_wait_ready(void);
int ufs_find_ready_lun(void);
int ufs_ssu(uint8_t lun);
int ufs_wake_lun(void);
int ufs_dme_get(uint32_t attr, uint32_t *val);
int ufs_dme_set(uint32_t attr, uint32_t val);
int ufs_negotiate(uint32_t *gear_out);
int ufs_tur_once(uint8_t lun, uint32_t *status);
int ufs_read_unit_desc(uint8_t lun, uint8_t *buf, uint16_t len);
int ufs_read_device_desc(uint8_t *buf, uint16_t len);
int cmd_db_entry(const char *name, uint32_t *addr_out, uint16_t *slv_out);
uint32_t ufs_reg_read(uint32_t off);
int gcc_ufs_bring_up(void);
int gcc_disp_bring_up(void);
int gcc_ufs_gdsc_on(void);
void gcc_restore(void);	/* put touched GCC regs back before resetting */
extern uint32_t ufs_cap_post, ufs_ver_post;
int ufs_utrl_base_matches(void);	/* full UFSHCI bring-up, 0 = ok */

/* Persistent log into the tail of an unused partition (see ufs.c). */
int klog_init(const char *part);
void klog(const char *tag, const char *msg);
void klog_hex(const char *tag, uint64_t v);
int klog_sync(void);
int ufs_probe_write(uint32_t lba, const void *buf, int upiu_flag, int utrd_dd);
int ufs_probe_read(uint32_t lba, void *buf);
int ufs_part_first(const char *name, uint32_t *first);
