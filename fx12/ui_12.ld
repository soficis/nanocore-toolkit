/* Linker script for the 1.2 defaults-writer blob (0x4007386a, 128 bytes).
 * The formatter now has its own script (vibe_format_12.ld) at the proven site.
 */
defaults_target = 0x4007386a;

ENTRY(vibe_defaults)
SECTIONS
{
 .vibe_defaults 0x4007386a : SUBALIGN(2) { *(.vibe_defaults) }
 ASSERT(SIZEOF(.vibe_defaults) <= 128, "defaults overflow")

 /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) *(.riscv.attributes) }
}
