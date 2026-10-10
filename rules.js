/**
 * rules.js —— 全局规则中心（下载规则 / 平台识别规则 / 嗅探规则）
 *
 * 本文件是整个扩展唯一的规则来源，被 background.js（Service Worker）与
 * content.js（页面隔离世界）同时加载。新增平台或调整下载行为时，
 * 只改这里即可，不需要碰抓取与下载的实现代码。
 *
 * 规则共五类：
 *   1. 平台识别规则  PLATFORMS：域名 → 平台（决定用哪个抓取脚本、文件名怎么拼）
 *   2. 媒体识别规则  MEDIA_URL_PATTERN：什么样的 URL 算“可下载的媒体”
 *   3. 排除规则      EXCLUDE_URL_PATTERN：广告 / 埋点 / 预览图等，直接忽略
 *   4. 文件名规则    buildFileName：各平台统一的文件名拼法
 *   5. 下载行为规则  DOWNLOAD_RULES：m3u8 处理、候选地址回退、并发等
 *
 * 注意：本文件同时跑在 Service Worker 与内容脚本里，只能用两者都支持的
 * API（纯函数 + 正则，不要用 window / document / chrome.*）。
 */

"use strict";

/* ================================================================== */
/* 一、平台识别规则                                                    */
/* ================================================================== */

/**
 * 平台表。`hosts` 用子串匹配（命中任意一个即认定为该平台），
 * `extractor` 是主世界抓取脚本（manifest.json 里按平台静态注册）。
 * 未命中任何平台的页面走“通用”流程：DOM 扫描 + 资源嗅探。
 */
const PLATFORMS = [
  { id: "douyin",      name: "抖音",      hosts: ["douyin.com"],                    extractor: "extractors/douyin.js",      defaultStrategy: "extractor" },
  { id: "kuaishou",    name: "快手",      hosts: ["kuaishou.com", "kwimgs.com"],     extractor: "extractors/kuaishou.js",    defaultStrategy: "extractor" },
  { id: "bilibili",    name: "哔哩哔哩",  hosts: ["bilibili.com", "bilivideo.com"],  extractor: "extractors/bilibili.js",    defaultStrategy: "extractor" },
  { id: "weibo",       name: "微博",      hosts: ["weibo.com"],                      extractor: "extractors/weibo.js",       defaultStrategy: "extractor" },
  { id: "xiaohongshu", name: "小红书",    hosts: ["xiaohongshu.com", "xhslink.com"],  extractor: "extractors/xiaohongshu.js", defaultStrategy: "extractor" },
  { id: "xigua",       name: "西瓜视频",  hosts: ["ixigua.com"],                     extractor: null,                         defaultStrategy: "sniff" },
  { id: "youtube",     name: "YouTube",   hosts: ["youtube.com", "youtu.be"],        extractor: "extractors/youtube.js",     defaultStrategy: "extractor" },
  { id: "tiktok",      name: "TikTok",    hosts: ["tiktok.com"],                     extractor: "extractors/tiktok.js",      defaultStrategy: "extractor" },
  { id: "vimeo",       name: "Vimeo",     hosts: ["vimeo.com", "player.vimeo.com"],  extractor: "extractors/vimeo.js",       defaultStrategy: "extractor" },
  { id: "twitch",      name: "Twitch",    hosts: ["twitch.tv"],                      extractor: "extractors/twitch.js",      defaultStrategy: "extractor" },
  { id: "instagram",   name: "Instagram", hosts: ["instagram.com"],                  extractor: "extractors/instagram.js",   defaultStrategy: "extractor" },
  { id: "facebook",    name: "Facebook",  hosts: ["facebook.com", "fb.watch"],       extractor: "extractors/facebook.js",    defaultStrategy: "extractor" },
  { id: "twitter",     name: "X",         hosts: ["twitter.com", "x.com"],           extractor: "extractors/twitter.js",     defaultStrategy: "extractor" },
];

/** 未命中平台时的兜底平台 */
const GENERIC_PLATFORM = { id: "generic", name: "通用", hosts: [], extractor: null, defaultStrategy: "sniff" };

const DEFAULT_SETTINGS = {
  format: "source",
  quality: "best",
  rememberPanelPosition: true,
  batchConcurrency: 4,
  platformStrategies: {},
};

const DOWNLOAD_STRATEGY_LABELS = {
  auto: "智能（平台默认）",
  extractor: "页面解析优先",
  sniff: "网络嗅探优先",
};

const QUALITY_LABELS = {
  best: "最高可用清晰度",
  "2160": "4K / 2160p 优先",
  "1440": "1440p 优先",
  "1080": "1080p 优先",
  "720": "720p 优先",
  "480": "480p 优先",
  smallest: "较小文件优先",
};

/**
 * 按页面 URL 识别平台。
 * @returns 平台对象（PLATFORMS 中的一项，或 GENERIC_PLATFORM）
 */
function detectPlatform(pageUrl) {
  const url = String(pageUrl || "").toLowerCase();
  for (const platform of PLATFORMS) {
    if (platform.hosts.some((host) => url.includes(host))) return platform;
  }
  return GENERIC_PLATFORM;
}

/* ================================================================== */
/* 二、媒体识别规则（什么样的 URL 算“可下载的视频/音频”）              */
/* ================================================================== */

/**
 * 媒体 URL 判定：路径以常见媒体扩展名结尾（允许带查询参数与 hash）。
 * 覆盖：直链 mp4 / webm / mov / flv / ts / m4s / m4a / mp3，
 * 以及流媒体播放列表 m3u8（走专门的 m3u8 下载流程）。
 *
 * 注意：很多 CDN 直链不带扩展名（如 /video/tos/...?mime_type=video_mp4），
 * 这类地址靠“资源嗅探”（performance resource entries 的 initiatorType、
 * 或页面 <video> 元素）兜底，不在这里硬匹配，避免误杀。
 */
// Standalone entries only: audio files and DASH/HLS fragments are not complete videos.
const MEDIA_URL_PATTERN = /\.(m3u8|mp4|m4v|webm|mov|flv|mkv)(\?|#|$)/i;
const FILE_MEDIA_EXT_PATTERN = /\.(m3u8|mp4|m4v|webm|mov|flv|mkv|ts|m4s)(\?|#|$)/i;

/** 从 URL 里提取媒体扩展名（小写，不带点）；匹配不上返回 "" */
function guessMediaExt(url) {
  const match = String(url || "").match(FILE_MEDIA_EXT_PATTERN);
  return match ? match[1].toLowerCase() : "";
}

/** 是否为流媒体播放列表（需要走分段下载合并流程） */
function isPlaylistUrl(url) {
  return guessMediaExt(url) === "m3u8";
}

/** 是否为“看起来像可下载媒体”的 URL（先过排除规则再用这个判断） */
function isMediaUrl(url) {
  return MEDIA_URL_PATTERN.test(String(url || ""));
}


/**
 * Trust the response's media type, not a video's page title or URL extension.
 * A generic octet-stream requires binary signature proof before admission.
 */
function isVideoMimeType(value) {
  const type = String(value || "").split(";")[0].trim().toLowerCase();
  return type.startsWith("video/") || type === "application/mp4" ||
    type === "application/vnd.apple.mpegurl" || type === "application/x-mpegurl";
}

/** Reject definite non-video responses before spending bandwidth on their body. */
function isRejectedMediaMimeType(value) {
  const type = String(value || "").split(";")[0].trim().toLowerCase();
  return type.startsWith("audio/") || type.startsWith("image/") ||
    type.startsWith("text/") ||
    /^application\/(json|xml|pdf|javascript|x-javascript|zip|xhtml\+xml)/.test(type);
}

/**
 * Inspect a small leading byte range. An MP4/WebM/FLV/TS signature is much
 * stronger evidence than a .mp4 URL; HTML errors must never be saved as video.
 * @returns {"video"|"invalid"|"unknown"}
 */
function sniffVideoSignature(bytes) {
  if (!bytes || !bytes.length) return "unknown";
  const first = String.fromCharCode(...bytes.slice(0, Math.min(80, bytes.length))).trimStart().toLowerCase();
  if (/^(<!doctype|<html|<\?xml|<\?php|<script|\{|"error"|access denied|forbidden)/.test(first)) return "invalid";
  if (bytes.length >= 8 && bytes[4] === 0x66 && bytes[5] === 0x74 &&
      bytes[6] === 0x79 && bytes[7] === 0x70) {
    const brand = String.fromCharCode(...bytes.slice(8, 12));
    return /^(M4A |M4B )/.test(brand) ? "invalid" : "video";
  }
  if (bytes.length >= 4 && bytes[0] === 0x1a && bytes[1] === 0x45 &&
      bytes[2] === 0xdf && bytes[3] === 0xa3) return "video"; // WebM/Matroska
  if (bytes.length >= 5 && first.startsWith("flv")) return (bytes[4] & 1) ? "video" : "invalid";
  if (bytes.length > 376 && bytes[0] === 0x47 && bytes[188] === 0x47 &&
      bytes[376] === 0x47) return "video"; // MPEG-TS (not standalone in sniffing)
  if (bytes.length >= 12 && first.startsWith("riff") &&
      String.fromCharCode(...bytes.slice(8, 12)) === "AVI ") return "video";
  if (bytes[0] === 0xff && bytes[1] === 0xd8 ||
      first.startsWith("id3") || first.startsWith("%pdf") ||
      first.startsWith("gif8") || first.startsWith("riff") ||
      bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47) {
    return "invalid";
  }
  return "unknown";
}

/**
 * HLS must contain actual media variants or segments, not just an audio-only
 * rendition manifest. A segment playlist without CODECS uses segment file
 * evidence; ambiguous encrypted/opaque segments remain unsupported to prove.
 */
function isHlsVideoPlaylist(text) {
  const value = String(text || "").trimStart();
  if (!value.startsWith("#EXTM3U")) return false;
  if (/^#EXT-X-STREAM-INF:/m.test(value)) {
    const streams = value.split(/\r?\n/).filter((line) => line.startsWith("#EXT-X-STREAM-INF:"));
    return streams.some((line) => {
      const codecs = line.match(/CODECS="([^"]+)"/i)?.[1] || "";
      return !codecs || /(avc1|avc3|hev1|hvc1|av01|vp0[89]|dvh1|dvhe)/i.test(codecs);
    });
  }
  if (!/^#EXTINF:/m.test(value)) return false;
  const firstSegment = value.split(/\r?\n/).map((line) => line.trim())
    .find((line) => line && !line.startsWith("#")) || "";
  return !!firstSegment && !/\.(aac|m4a|mp3|wav|ogg)(\?|#|$)/i.test(firstSegment);
}

/* ================================================================== */
/* 三、排除规则（广告 / 埋点 / 缩略图，一律不收录）                    */
/* ================================================================== */

/**
 * 命中即忽略。经验值：
 * - 广告与统计域名：doubleclick / googlesyndication / analytics / beacon
 * - 预览/缩略图：thumb / preview / sprite / poster（通常是小图或雪碧图）
 * - 弹幕/字幕/封面：danmaku / subtitle / vtt（文本轨，下载无意义）
 */
const EXCLUDE_URL_PATTERN =
  /(doubleclick|googlesyndication|google-analytics|analytics|beacon|tracker|pixel|thumb|preview|sprite|poster|danmaku|subtitle|\.vtt(\?|#|$))/i;

/** 该 URL 是否应被嗅探忽略 */
function shouldIgnoreUrl(url) {
  return EXCLUDE_URL_PATTERN.test(String(url || ""));
}

/* ================================================================== */
/* 四、文件名规则                                                      */
/* ================================================================== */

/** 文件名非法字符清理（Windows / macOS 通用） */
function sanitizeFileName(name) {
  return String(name || "")
    .replace(/[\r\n]+/g, "_")
    .replace(/[/\\:*?"<>|#]+/g, "_")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80);
}

/** 时间戳后缀：20261002_143005 */
function timeSuffix(date) {
  const d = date || new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

/**
 * 统一文件名拼法（不带扩展名，调用方按媒体类型追加扩展名）：
 *   有作者/标题 → `作者_标题`
 *   否则        → `平台名_时间戳`（保证不重名、可排序）
 */
function buildFileName(platformName, author, title) {
  const cleanAuthor = sanitizeFileName(author);
  const cleanTitle = sanitizeFileName(title);
  if (cleanAuthor || cleanTitle) {
    return `${cleanAuthor}_${cleanTitle}`.replace(/^_+|_+$/g, "") || `${platformName}_${timeSuffix()}`;
  }
  return `${platformName}_${timeSuffix()}`;
}

/* ================================================================== */
/* 五、下载行为规则                                                    */
/* ================================================================== */

const DOWNLOAD_RULES = {
  /** m3u8 分段下载并发数（分段通常很小，并发高一点没关系） */
  M3U8_SEGMENT_CONCURRENCY: 6,

  /** Video identity probes are capped and never fetch a whole file. */
  MEDIA_PROBE_TIMEOUT_MS: 8000,
  MEDIA_PROBE_MAX_BYTES: 16384,
  MEDIA_PROBE_CONCURRENCY: 3,

  /** Stop a direct transfer after this long without a received byte. */
  DIRECT_IDLE_TIMEOUT_MS: 20000,

  /** yt-dlp and cobalt retry transient transfers; cap full-video retries to avoid excess bandwidth. */
  DIRECT_RETRIES: 1,

  /** Retry failed HLS fragments independently rather than restarting an entire playlist. */
  M3U8_SEGMENT_RETRIES: 2,
  M3U8_PLAYLIST_RETRIES: 1,

  /** Bounded exponential backoff for retryable network/HTTP failures. */
  TRANSFER_RETRY_BASE_DELAY_MS: 400,
  TRANSFER_RETRY_MAX_DELAY_MS: 1600,

  /** Prevent concurrent multi-hundred-MB Blobs from overwhelming a tab. */
  MAX_INFLIGHT_MEDIA_BYTES: 512 * 1024 * 1024,

  /** 单个分段下载超时（毫秒），超时则整体失败并提示用户 */
  M3U8_SEGMENT_TIMEOUT_MS: 30000,

  /** m3u8 播放列表里出现加密 KEY（AES-128 等）时直接放弃，并如实提示 */
  M3U8_ENCRYPTED_MESSAGE: "该视频为加密流（m3u8 EXT-X-KEY），暂不支持下载",

  /** 直链下载：依次尝试候选地址（不同清晰度/CDN），直到成功为止 */
  DIRECT_TRY_ALL_CANDIDATES: true,

  /** 疑似空文件阈值：小于 1KB 的响应视为失败，继续试下一个候选地址 */
  MIN_VALID_BYTES: 1024,

  /** 批量下载线程数上下限（具体值仍由 content.js 按浏览器内存预算自适应） */
  MIN_CONCURRENCY: 2,
  MAX_CONCURRENCY: 16,
};

/* ================================================================== */
/* 导出（Service Worker 用 importScripts，内容脚本靠 manifest 顺序加载）*/
/* ================================================================== */

/* eslint-disable no-unused-vars */
// 在 MV3 Service Worker 里：importScripts("rules.js") 后直接使用全局量；
// 在内容脚本里：manifest 的 js 数组保证 rules.js 先于 content.js 执行。
// 这里不做模块导出，保持“无构建、直改即生效”。
