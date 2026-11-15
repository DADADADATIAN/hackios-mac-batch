/* SPDX-License-Identifier: BSD-2-Clause-Patent */
/* Local 21H16 caller API for the donor integer SSC clock mechanism.
 * Context and cursors are trusted caller-owned storage, serialized by one
 * worker, never supplied directly by an untrusted user client. No I/O here.
 * Confirming an epoch requires separately retained current-boot/domain
 * evidence. Brackets and sample arrival alone do not establish that evidence.
 * AP sleep, lost continuity and SLPI restart require invalidate and fresh proof.
 */
#ifndef NABU_SENSOR_CLOCK_H
#define NABU_SENSOR_CLOCK_H
#include <stdint.h>
#ifndef NABU_SENSOR_CLOCK_CORE
#define NABU_SENSOR_CLOCK_CORE 0
#endif
#if NABU_SENSOR_CLOCK_CORE != 0 && NABU_SENSOR_CLOCK_CORE != 1
#error NABU_SENSOR_CLOCK_CORE_must_be_zero_or_one
#endif
enum { NabuSensorClockDisabled=-1, NabuSensorClockBadArgument=-2,
       NabuSensorClockEpochRefused=-3, NabuSensorClockStaleGeneration=-4,
       NabuSensorClockAlreadyInitialized=-5 };
enum { NabuSensorClockUnknown=0, NabuSensorClockVirtual=1, NabuSensorClockPhysical=2 };
enum { NabuSensorClockInitial=0, NabuSensorClockRefreshed=1, NabuSensorClockDiscontinuity=2,
       NabuSensorClockPolicyPending=3, NabuSensorClockWideBracket=4,
       NabuSensorClockBadFrequency=5, NabuSensorClockBadOrder=6, NabuSensorClockOverflow=7 };
enum { NabuSensorClockConverted=0, NabuSensorClockEpochPending=1, NabuSensorClockObservationPending=2,
       NabuSensorClockAgePending=3, NabuSensorClockBeforeSegment=4, NabuSensorClockFuture=5,
       NabuSensorClockTooOld=6, NabuSensorClockNonMonotonic=7, NabuSensorClockConversionOverflow=8 };
typedef union { uint64_t alignment; unsigned char storage[256]; } NabuSensorClockContext;
typedef struct { uint64_t mach_before,physical_ticks,virtual_ticks,frequency,mach_after; } NabuSensorClockBracket;
typedef struct { uint64_t generation,ssc_ticks,mach_ticks; uint32_t seen,reserved; } NabuSensorClockCursor;
typedef struct { uint64_t mach_ticks,earliest_mach,latest_mach,age_ticks,generation; } NabuSensorClockStamp;
#ifdef __cplusplus
extern "C" {
#endif
/* source ID binds canonical source hashes AND the compiled enabled value. */
extern const char nabu_sensor_clock_build_identity[];
extern const uint64_t nabu_sensor_clock_build_policy[2];
const char *nabu_sensor_clock_source_id(void);
int32_t nabu_sensor_clock_enabled(void);
/* Zero-fill fresh storage before init. A live context cannot be reinitialized;
 * invalidate preserves/increments its generation across sleep or DSP restart.
 * Cursor/proof state must also remain bound to this specific owner and boot.
 */
int32_t nabu_sensor_clock_init(NabuSensorClockContext *);
/* Output/input overlap returns BadArgument without changing either range. */
int32_t nabu_sensor_clock_generation(const NabuSensorClockContext *,uint64_t *);
/* Returns an Update value >=0 or a negative API error. Bounds are caller policy. */
int32_t nabu_sensor_clock_observe(NabuSensorClockContext *,const NabuSensorClockBracket *,uint64_t max_bracket_ticks,uint64_t read_uncertainty_ticks);
int32_t nabu_sensor_clock_confirm_epoch(NabuSensorClockContext *,uint64_t expected_generation,uint32_t domain);
int32_t nabu_sensor_clock_invalidate(NabuSensorClockContext *);
/* Returns Converted only with an independently confirmed epoch. Stamp is zero
 * and cursor is unchanged on rejection. Outputs must not alias context/input.
 * Overlapping context, cursor or stamp returns BadArgument with all unchanged.
 * max_age_ticks=0 is pending; UINT64_MAX still enforces the current segment.
 */
int32_t nabu_sensor_clock_convert(const NabuSensorClockContext *,uint64_t expected_generation,uint64_t ssc_ticks,NabuSensorClockCursor *,uint64_t max_age_ticks,NabuSensorClockStamp *);
int32_t nabu_sensor_clock_ticks_to_ns(uint64_t,uint64_t *);
int32_t nabu_sensor_clock_ns_to_ticks(uint64_t,uint64_t *);
#ifdef __cplusplus
}
#endif
#endif
