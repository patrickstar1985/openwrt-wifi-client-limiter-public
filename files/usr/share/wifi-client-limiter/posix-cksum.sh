#!/bin/sh
# SPDX-License-Identifier: MIT
# Preserve legacy CRC:length defaults fingerprints on BusyBox builds that
# omit cksum. Read bytes on stdin; return no fingerprint on any provider error.

internet_checksum_record() {
 local value="$1" format="$2"
 printf '%s\n' "$value" | LC_ALL=C awk -v format="$format" '
  NR!=1 {bad=1;exit 1}
  {
   if(format=="colon") {
    if(split($0,fields,":")!=2) {bad=1;exit 1}
    crc=fields[1]; size=fields[2]
   } else {
    if(NF!=2) {bad=1;exit 1}
    crc=$1; size=$2
   }
   if(crc !~ /^[0-9]+$/ || size !~ /^[0-9]+$/ ||
      crc+0>4294967295 || size+0>9007199254740991) {bad=1;exit 1}
  }
  END {
   if(bad || NR!=1) exit 1
   printf "%.0f:%.0f\n",crc+0,size+0
  }'
}

internet_posix_cksum() {
 local bytes result
 command -v awk >/dev/null 2>&1 || return 1
 if command -v cksum >/dev/null 2>&1; then
  # Capture status before parsing: a final awk must not hide provider failure.
  result="$(LC_ALL=C cksum)" || return 1
  result="$(internet_checksum_record "$result" fields)" || return 1
  printf '%s\n' "$result"
  return "$?"
 fi
 command -v hexdump >/dev/null 2>&1 || return 1
 # This byte format is supported by stock OpenWrt/GL BusyBox, including NUL.
 # A separate capture prevents a partial failed dump from yielding a checksum.
 bytes="$(LC_ALL=C hexdump -v -e '1/1 "%u\n"')" || return 1
 result="$(printf '%s\n' "$bytes" | LC_ALL=C awk '
 function bxor(a,b,    result,place) {
  result=0; place=1
  while(a>0 || b>0) {
   result+=nibble[(a%16)*16+(b%16)]*place
   a=int(a/16); b=int(b/16); place*=16
  }
  return result
 }
 function update(crc,byte,    bucket) {
  bucket=bxor(int(crc/16777216),byte)
  return bxor((crc%16777216)*256,table[bucket])
 }
 BEGIN {
  # Ordinary POSIX awk arithmetic, with no bitwise extension requirement.
  for(a=0;a<16;a++) for(b=0;b<16;b++) {
   left=a; right=b; value=0; place=1
   for(bit=0;bit<4;bit++) {
    if(left%2!=right%2) value+=place
    left=int(left/2); right=int(right/2); place*=2
   }
   nibble[a*16+b]=value
  }
  for(i=0;i<256;i++) {
   value=i*16777216
   for(bit=0;bit<8;bit++) {
    value*=2
    if(value>=4294967296) value=bxor(value-4294967296,79764919)
   }
   table[i]=value
  }
  crc=0; length_bytes=0
 }
 NF==0 {next}
 $0 !~ /^[0-9]+$/ || $0+0>255 {bad=1;exit 1}
 {crc=update(crc,$0+0);length_bytes++}
 END {
  if(bad) exit 1
  # POSIX cksum folds the original length in least-significant-byte order.
  remaining=length_bytes
  while(remaining>0) {
   crc=update(crc,remaining%256)
   remaining=int(remaining/256)
  }
  printf "%.0f:%.0f\n",4294967295-crc,length_bytes
 }')" || return 1
 result="$(internet_checksum_record "$result" colon)" || return 1
 printf '%s\n' "$result"
}
