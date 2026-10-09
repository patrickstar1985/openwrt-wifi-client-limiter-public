/* SPDX-License-Identifier: MIT */
ENTRY(_start)
PHDRS
{
    text PT_LOAD FILEHDR PHDRS FLAGS(5);
    stack PT_GNU_STACK FLAGS(6);
}
SECTIONS
{
    . = 0x400000 + SIZEOF_HEADERS;
    .text : { *(.text .text.*) } :text
    /DISCARD/ : { *(.comment) *(.note*) *(.ARM.attributes) }
}
