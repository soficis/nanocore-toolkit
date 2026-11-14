/* NanoCore 1.2 MOD10 UI: defaults writer for the UniVibe slot.
 * Standalone rewrite of the 0x4007386a writer slot (128 bytes). No count/enum hook
 * exists in 1.2, so this blob carries only the writer plus its data and two labels.
 * Mirrors tools/custom_fx/vibe_ui_multi.S (defaults section) minus vibe_count.
 */
.option norelax

.section .vibe_defaults,"ax",@progbits
.globl vibe_defaults, vibe_speed_label, vibe_mode_label
vibe_defaults:
 beqz a0, 3f
 li t0, 0
 la t1, defaults
1:
 bgeu t0, a1, 3f
 li t2, 0
 li t3, 4
 bgeu t0, t3, 2f
 lw t2, 0(t1)
 addi t1,t1,4
2:
 sw t2, 0(a0)
 addi a0,a0,4
 addi t0,t0,1
 j 1b
3:
 ret

.balign 4
.short 0            /* section may start 2 mod 4; keep the float table aligned */
defaults:
 .float 0.35,0.8,0.5,0.0
vibe_speed_label: .asciz "SPEED"
vibe_mode_label:  .asciz "MODE"
