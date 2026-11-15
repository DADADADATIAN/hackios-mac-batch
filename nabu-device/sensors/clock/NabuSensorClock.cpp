/* SPDX-License-Identifier: BSD-2-Clause-Patent */
/* Integer-only wrapper around NabuSscClockCore.h. Provenance is in
 * the migration source receipt; no Darwin 22 private addresses/layouts or I/O.
 */
#include "NabuSensorClock.h"
#include "NabuSensorClockBuild.h"
#include <string.h>
#if NABU_SENSOR_CLOCK_PREPARED_ENABLED != NABU_SENSOR_CLOCK_CORE
#error Sensor_clock_prepare_and_compile_gates_must_match
#endif
namespace {
// Do not clear output storage until it has been checked against live inputs.
// The subtraction form also avoids wrapping the end of a uintptr_t interval.
bool overlaps(const void *a,size_t as,const void *b,size_t bs) {
    if(!a||!b)return false;
    const uintptr_t x=reinterpret_cast<uintptr_t>(a),y=reinterpret_cast<uintptr_t>(b);
    return x>=y ? x-y<bs : y-x<as;
}
static_assert(sizeof(NabuSensorClockContext)==256,"C context ABI");
static_assert(sizeof(NabuSensorClockBracket)==40,"C bracket ABI");
static_assert(sizeof(NabuSensorClockCursor)==32,"C cursor ABI");
static_assert(sizeof(NabuSensorClockStamp)==40,"C stamp ABI");
}
extern "C" {
__attribute__((used)) const char nabu_sensor_clock_build_identity[]=NABU_SENSOR_CLOCK_SOURCE_ID;
__attribute__((used)) const uint64_t nabu_sensor_clock_build_policy[2]={1,NABU_SENSOR_CLOCK_CORE};
const char *nabu_sensor_clock_source_id(void) { return nabu_sensor_clock_build_identity; }
int32_t nabu_sensor_clock_enabled(void) { return NABU_SENSOR_CLOCK_CORE; }
}
#if NABU_SENSOR_CLOCK_CORE
#include "NabuSscClockCore.h"
namespace {
struct Storage { uint64_t tag=UINT64_C(0x534343434c4b0001); NabuSsc::ClockMapping mapping{}; };
static_assert(sizeof(Storage)<=sizeof(NabuSensorClockContext),"bounded caller context");
static_assert(__is_trivially_copyable(Storage),"serialized context has no owned pointers");
bool load(const NabuSensorClockContext *context,Storage &storage) {
    if(!context)return false;
    uint64_t tag;memcpy(&tag,context->storage,sizeof(tag));
    if(tag!=UINT64_C(0x534343434c4b0001))return false;
    memcpy(&storage,context->storage,sizeof(storage));return true;
}
void save(NabuSensorClockContext *context,const Storage &storage) {
    memset(context,0,sizeof(*context));memcpy(context->storage,&storage,sizeof(storage));
}
}
#endif
extern "C" int32_t nabu_sensor_clock_init(NabuSensorClockContext *context) {
    if(!context)return NabuSensorClockBadArgument;
#if NABU_SENSOR_CLOCK_CORE
    Storage existing;
    if(load(context,existing))return NabuSensorClockAlreadyInitialized;
    for(unsigned char byte:context->storage)if(byte)return NabuSensorClockBadArgument;
    save(context,Storage{});return 0;
#else
    memset(context,0,sizeof(*context));
    return NabuSensorClockDisabled;
#endif
}
extern "C" int32_t nabu_sensor_clock_generation(const NabuSensorClockContext *context,uint64_t *generation) {
    if(overlaps(context,sizeof(*context),generation,sizeof(*generation)))return NabuSensorClockBadArgument;
    if(generation)*generation=0;
    if(!context||!generation)return NabuSensorClockBadArgument;
#if NABU_SENSOR_CLOCK_CORE
    Storage storage;if(!load(context,storage))return NabuSensorClockBadArgument;
    *generation=storage.mapping.generation();return 0;
#else
    return NabuSensorClockDisabled;
#endif
}
extern "C" int32_t nabu_sensor_clock_observe(NabuSensorClockContext *context,const NabuSensorClockBracket *bracket,uint64_t max_bracket_ticks,uint64_t uncertainty) {
    if(!context||!bracket)return NabuSensorClockBadArgument;
    if(overlaps(context,sizeof(*context),bracket,sizeof(*bracket)))return NabuSensorClockBadArgument;
#if NABU_SENSOR_CLOCK_CORE
    Storage storage;if(!load(context,storage))return NabuSensorClockBadArgument;
    NabuSsc::ClockBracket b{bracket->mach_before,bracket->physical_ticks,bracket->virtual_ticks,bracket->frequency,bracket->mach_after};
    const auto result=storage.mapping.observe(b,max_bracket_ticks,uncertainty);
    save(context,storage);return int32_t(result);
#else
    (void)max_bracket_ticks;(void)uncertainty;return NabuSensorClockDisabled;
#endif
}
extern "C" int32_t nabu_sensor_clock_confirm_epoch(NabuSensorClockContext *context,uint64_t generation,uint32_t domain) {
    if(!context||domain>NabuSensorClockPhysical)return NabuSensorClockBadArgument;
#if NABU_SENSOR_CLOCK_CORE
    Storage storage;if(!load(context,storage))return NabuSensorClockBadArgument;
    if(generation!=storage.mapping.generation())return NabuSensorClockStaleGeneration;
    const bool accepted=storage.mapping.confirm_epoch(generation,NabuSsc::ClockEpoch(domain));
    save(context,storage);return accepted?0:NabuSensorClockEpochRefused;
#else
    (void)generation;return NabuSensorClockDisabled;
#endif
}
extern "C" int32_t nabu_sensor_clock_invalidate(NabuSensorClockContext *context) {
    if(!context)return NabuSensorClockBadArgument;
#if NABU_SENSOR_CLOCK_CORE
    Storage storage;if(!load(context,storage))return NabuSensorClockBadArgument;
    storage.mapping.invalidate();save(context,storage);return 0;
#else
    return NabuSensorClockDisabled;
#endif
}
extern "C" int32_t nabu_sensor_clock_convert(const NabuSensorClockContext *context,uint64_t generation,uint64_t ticks,NabuSensorClockCursor *cursor,uint64_t max_age,NabuSensorClockStamp *stamp) {
    if(overlaps(context,sizeof(*context),cursor,sizeof(*cursor))||
       overlaps(context,sizeof(*context),stamp,sizeof(*stamp))||
       overlaps(cursor,sizeof(*cursor),stamp,sizeof(*stamp)))return NabuSensorClockBadArgument;
    if(stamp)memset(stamp,0,sizeof(*stamp));
    if(!context||!cursor||!stamp||cursor->seen>1||cursor->reserved)return NabuSensorClockBadArgument;
#if NABU_SENSOR_CLOCK_CORE
    Storage storage;if(!load(context,storage))return NabuSensorClockBadArgument;
    if(generation!=storage.mapping.generation())return NabuSensorClockStaleGeneration;
    NabuSsc::ClockCursor c{cursor->seen!=0,cursor->generation,cursor->ssc_ticks,cursor->mach_ticks};
    NabuSsc::ClockStamp output;
    const auto result=storage.mapping.convert(ticks,c,max_age,output);
    if(result==NabuSsc::ClockSampleResult::Converted) {
        *cursor={c.generation,c.ssc_ticks,c.mach_ticks,uint32_t(c.seen),0};
        *stamp={output.mach_ticks,output.earliest_mach,output.latest_mach,output.age_ticks,output.generation};
    }
    return int32_t(result);
#else
    (void)generation;(void)ticks;(void)max_age;return NabuSensorClockDisabled;
#endif
}
extern "C" int32_t nabu_sensor_clock_ticks_to_ns(uint64_t ticks,uint64_t *ns) {
    if(!ns)return NabuSensorClockBadArgument;
    *ns=0;
#if NABU_SENSOR_CLOCK_CORE
    return NabuSsc::clock_ticks_to_nanoseconds(ticks,*ns)?0:NabuSensorClockConversionOverflow;
#else
    (void)ticks;return NabuSensorClockDisabled;
#endif
}
extern "C" int32_t nabu_sensor_clock_ns_to_ticks(uint64_t ns,uint64_t *ticks) {
    if(!ticks)return NabuSensorClockBadArgument;
    *ticks=0;
#if NABU_SENSOR_CLOCK_CORE
    *ticks=NabuSsc::clock_nanoseconds_to_ticks(ns);return 0;
#else
    (void)ns;return NabuSensorClockDisabled;
#endif
}
