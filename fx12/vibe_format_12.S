/* NanoCore 1.2 MOD subtype-10 value formatter.
 *
 * Region 0x4002d196..0x4002d1da (68 bytes, FIT 0x44996..0x449da). The 8-byte
 * subtype guard at 0x4002d190 (`li a5,10; bne s3,a5,0x4002d1da`) is NOT touched,
 * and nothing may cross into the subtype-3 arm at 0x4002d1da.
 *
 * Contract inside the region:
 *   s0  = parameter index
 *   s1  = display buffer
 *   s2  = buffer size
 *   fa0 = value, 0..1
 * Return: a3 = 0 (the 0x4002bcca epilogue does `mv a0,a3`), a0 = 0, `j 0x4002bcca`.
 *
 * Control 3 (MODE) renders CHORUS below 0.5 and VIBRATO at/above 0.5 through the
 * same snprintf("%s") path 1.12 uses. Every other control uses the native
 * percentage renderer 0x4002a1ba. The stock index-0 pow/Hz special case is
 * dropped for space: SPEED now reads as 0-100% instead of an exponential number.
 */
.option norelax

.equ chorus_lbl,        0x40089de4      /* "CHORUS"  */
.equ vibrato_lbl,       0x4008a268      /* "VIBRATO" */
.equ fmt_s,             0x40089d28      /* "%s"      */
.equ native_snprintf,   0x4000d3de
.equ native_percentage, 0x4002a1ba
.equ format_return,     0x4002bcca

.section .text.vibe_format,"ax",@progbits
.globl vibe_format
vibe_format:
 li   a5, 3
 bne  s0, a5, pct            /* only MODE is a two-state string */
 lui  a5, 0x3f000
 fmv.w.x fa5, a5             /* 0.5f */
 flt.s a5, fa0, fa5
 la   a3, vibrato_lbl        /* default label: VIBRATO (0x4008a268) */
 beqz a5, 1f
 addi a3, a3, -0x484         /* fa0 < 0.5 -> CHORUS (0x40089de4) */
1:
 li   a2, fmt_s              /* "%s" */
 mv   a1, s2
 mv   a0, s1
 jal  ra, native_snprintf
 j    done
pct:
 mv   a0, s1
 mv   a1, s2
 jal  ra, native_percentage  /* fa0 carries the value */
done:
 li   a3, 0                  /* status consumed by 0x4002bcca (mv a0,a3) */
 li   a0, 0
 j    format_return
