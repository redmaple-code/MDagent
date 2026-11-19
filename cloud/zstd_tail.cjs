#!/usr/bin/env node
// zstd_tail.cjs <file>:流式解压持续追加的 zstd 帧流,按行输出到 stdout。
// dsh session.jsonl.zstd 每次 flush 是一个独立 zstd 帧(魔数 28 B5 2F FD)追加到文件尾,
// 单个 createZstdDecompress 只吃首帧——这里按魔数切帧,每帧一个解压器。
// 任何出错静默 exit 0(调用方 runner 有 stdout 兜底)。
"use strict";
const fs = require("fs");
const zlib = require("zlib");

const target = process.argv[2];
if (!target) process.exit(0);

const MAGIC = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);

let lineBuf = "";
function onText(chunk) {
  lineBuf += chunk.toString("utf8");
  let i;
  while ((i = lineBuf.indexOf("\n")) >= 0) {
    process.stdout.write(lineBuf.slice(0, i + 1));
    lineBuf = lineBuf.slice(i + 1);
  }
}

let dec = null;       // 当前帧解压器
let frameOpen = false;
let rawCarry = Buffer.alloc(0);
let stopped = false;

function makeDec() {
  const d = zlib.createZstdDecompress();
  d.on("data", onText);
  d.on("error", stop);
  return d;
}

function feedRaw(chunk) {
  rawCarry = Buffer.concat([rawCarry, chunk]);
  for (;;) {
    if (!frameOpen) {
      if (rawCarry.length < 4) return;
      const idx = rawCarry.indexOf(MAGIC);
      if (idx < 0) { rawCarry = rawCarry.subarray(rawCarry.length - 3); return; }
      rawCarry = rawCarry.subarray(idx);
      dec = makeDec();
      frameOpen = true;
    }
    const next = rawCarry.indexOf(MAGIC, 1);
    if (next < 0) {
      // 尾部 3 字节可能是跨块魔数,留着不喂
      const n = Math.max(0, rawCarry.length - 3);
      if (n > 0) dec.write(rawCarry.subarray(0, n));
      rawCarry = rawCarry.subarray(n);
      return;
    }
    dec.end(rawCarry.subarray(0, next));  // 本帧完整,收尾
    rawCarry = rawCarry.subarray(next);
    frameOpen = false;
    dec = null;
  }
}

let offset = 0;
let fd = null;

function pump() {
  if (stopped) return;
  if (fd === null) {
    try {
      fd = fs.openSync(target, "r");
    } catch (e) {
      fd = null;
      return; // 等下一轮
    }
  }
  const buf = Buffer.alloc(65536);
  let n;
  try {
    n = fs.readSync(fd, buf, 0, buf.length, offset);
  } catch (e) {
    stop();
    return;
  }
  if (n > 0) {
    offset += n;
    feedRaw(buf.subarray(0, n));
  }
}

function stop() {
  if (stopped) return;
  stopped = true;
  if (fd !== null) {
    try { fs.closeSync(fd); } catch (e) {}
    fd = null;
  }
  clearInterval(timer);
  setImmediate(() => process.exit(0));
}

// 文件可能在 headless 启动后几秒才出现;30s 等不到就放弃(退化旧行为)
const deadline = Date.now() + 30000;
const timer = setInterval(() => {
  pump();
  if (fd === null && Date.now() > deadline) stop();
}, 200);

process.on("SIGTERM", stop);
