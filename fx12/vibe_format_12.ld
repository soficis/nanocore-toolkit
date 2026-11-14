/* Verbatim 4-assert template from tools/custom_fx/build.py:41-52, instantiated for the
 * proven 1.2 MOD subtype-10 formatter region.
 */
ENTRY(vibe_format)
SECTIONS {
 . = 0x4002d196;
 .payload 0x4002d196 : SUBALIGN(2) { *(.text.vibe_format) *(.text*) . = ALIGN(4); *(.rodata*) *(.srodata*) }
 .forbidden : { *(.data*) *(.sdata*) *(.bss*) *(.sbss*) *(COMMON) }
 /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
 ASSERT(ADDR(.payload) == 0x4002d196, "entry address moved")
 ASSERT(vibe_format == 0x4002d196, "entry is not first")
 ASSERT(SIZEOF(.payload) <= 68, "replacement exceeds native routine")
 ASSERT(SIZEOF(.forbidden) == 0, "persistent globals prohibited")
}
