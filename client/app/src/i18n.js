// Two dictionaries and a lookup.
//
// The prototype did this by writing the English into a `data-en` attribute on
// whichever element held the Chinese, then overwriting `textContent` after
// every render. That had a bug with a shape worth remembering: any element
// holding an interpolated value *and* a `data-en` lost the value, because
// setting `textContent` replaces the children React had just put there. The
// search view's "位置" column was relabelled "Modified" in English for exactly
// that reason -- the translation clobbered the interpolation.
//
// A dictionary cannot do that. Interpolation happens through arguments, so
// there is nothing for a translation to overwrite.
//
// The register is deliberately plain: what the reader has to decide or do,
// never how it is implemented. Chunk sizes, cipher names, Argon2, parent_id
// and MongoDB were all in here once. None of them changed what anybody
// clicked. The exceptions that stayed are the ones where the mechanism *is*
// the decision: losing the password loses the files, a size in the listing is
// not a size that has been checked, and a failed check is not a network
// glitch you can retry past.

import { useCallback } from "react";

const DICT = {
  zh: {
    "app.name": "Discord Drive",
    "app.tagline": "檔案在你登入之後才解得開。",

    "nav.back": "上一頁",
    "nav.forward": "下一頁",
    "nav.up": "上一層",
    "nav.refresh": "重新整理",
    "nav.search": "搜尋整個硬碟",
    "nav.newFolder": "新資料夾",
    "nav.upload": "上傳",
    "nav.list": "清單",
    "nav.grid": "格狀",

    "side.myDrive": "我的雲端硬碟",
    "side.system": "系統",
    "side.trash": "垃圾桶",

    "col.name": "名稱",
    "col.size": "大小",
    "col.modified": "修改時間",
    "col.location": "位置",
    "col.kind": "種類",
    "col.integrity": "檢查",
    "col.remaining": "剩餘時間",

    "empty.title": "這裡還沒有東西",
    "empty.hint": "把檔案拖到這個視窗，或用上面的「上傳」。",
    "empty.explainTitle": "你的檔案會怎麼被保護",
    "empty.explainBody":
      "檔案會先加密再存到 Discord，鑰匙只留在伺服器。就算有人直接拿到 Discord 上的檔案，也讀不出內容；被動過手腳的檔案，下載時會被擋下來。",

    "login.username": "使用者名稱",
    "login.password": "密碼",
    "login.passwordNote":
      "這組密碼是解開所有檔案的鑰匙。忘記它，檔案就再也打不開——沒有任何人能幫你救回來。",
    "login.ttl": "這次登入要維持多久",
    "login.ttlNote":
      "伺服器允許閒置 {idle}、總共 {absolute}。可以選更短的，但不能更長。",
    "login.submit": "登入",
    "login.working": "驗證中…",
    "login.max": "（上限）",
    "login.serverDown.title": "連不上伺服器",
    "login.serverDown.body":
      "如果一直失敗，多半是伺服器正在重新啟動，等一下再試。",

    "login.err.401.title": "使用者名稱或密碼錯誤",
    "login.err.401.body": "請再確認一次。伺服器不會說錯的是哪一邊。",
    "login.err.429.title": "暫時被鎖住了",
    "login.err.429.body":
      "這台裝置錯太多次了，{retry} 秒後可以再試。你的帳號本身沒有被鎖。",
    "login.err.503.title": "伺服器忙不過來",
    "login.err.503.body": "同時登入的人太多，稍等一下再試。",
    "login.err.other.title": "登入失敗",

    "status.items": "{n} 個項目",
    "status.selected": "已選 {n}",
    "status.trash": "垃圾桶 · {n} 個項目 · {size}",
    "status.search": "{n} 個結果",
    "status.searchTruncated": "{n} 個結果（還有更多，已截斷）",
    "status.verified": "清單已核對",
    "status.integrity": "{n} 項有問題",
    "status.idle": "閒置",
    "status.absolute": "總計",
    "status.connections": "{n} 個連線",
    "status.offline": "與伺服器失去聯絡",

    "detail.title": "詳細資訊",
    "detail.none": "沒有選取項目",
    "detail.download": "下載",
    "detail.rename": "重新命名",
    "detail.delete": "刪除",
    "detail.kind": "種類",
    "detail.size": "大小",
    "detail.chunks": "分塊數",
    "detail.modified": "修改時間",
    "detail.permissions": "權限",
    "detail.path": "路徑",
    "detail.multi": "選取了 {n} 個項目",
    "detail.totalSize": "總大小",
    "detail.moveToTrash": "移到垃圾桶",
    "detail.unverified": "尚未檢查",
    // 這一條同時是清單上盾牌的 tooltip、詳細資訊面板的內文，以及狀態列那顆
    // 藥丸的 tooltip，所以它必須自己站得住，不能依賴旁邊的字。
    "detail.unverifiedNote":
      "這個資料夾裡有哪些項目已經核對過，但每個項目的內容還沒有檢查——下載的時候才會真的驗一次。",
    "detail.verified": "已通過檢查",
    "detail.verifiedNote":
      "這個檔案剛才讀取時通過了檢查：內容和檔名都跟存進來的時候一致。",

    "trash.hint":
      "垃圾桶裡的東西可以還原回原本的位置。保留期滿之後才會真的刪掉，空間也才會釋放。",
    "trash.empty": "清空垃圾桶",
    "trash.restore": "還原",
    "trash.purge": "永久刪除",
    "trash.nothing": "垃圾桶是空的",
    "trash.expiresIn": "{n} 天後永久刪除",
    "trash.expiresTomorrow": "明天永久刪除",
    "trash.expiresDue": "即將永久刪除",
    "trash.retention": "保留 {n} 天",

    "search.placeholder": "輸入檔名的一部分",
    "search.title": "搜尋整個硬碟",
    "search.hint": "只比對檔名，不搜尋檔案內容。",
    "search.truncated":
      "結果太多，只顯示前 {n} 筆。把關鍵字打長一點。",
    "search.none": "沒有符合的項目",
    "search.scanned": "找過 {n} 個項目",
    "search.reveal": "在資料夾中顯示",

    "transfer.title": "傳輸",
    "transfer.uploading": "上傳中",
    "transfer.done": "完成",
    "transfer.failed": "失敗",
    "transfer.cancelled": "已取消",
    "transfer.cancel": "取消",
    "transfer.clear": "清除已完成",
    "transfer.note":
      "進度是已經送出的量。送完之後伺服器還要存一下，才會出現在清單裡。",

    "dlg.cancel": "取消",
    "dlg.newFolder": "新資料夾名稱",
    "dlg.create": "建立",
    "dlg.rename": "重新命名",
    "dlg.renameTo": "新名稱",
    "dlg.confirmRename": "改名",
    "dlg.renameNote": "改名不會蓋掉既有的檔案；名字已經有人用了就會被拒絕。",

    "dlg.trash.title": "移到垃圾桶？",
    "dlg.trash.one": "「{name}」",
    "dlg.trash.many": "{n} 個項目",
    "dlg.trash.note": "之後可以還原。保留期滿才會真的刪掉。",
    "dlg.trash.go": "移到垃圾桶",
    "dlg.trash.recursive":
      "這個資料夾裡還有東西，會整個移進垃圾桶。",

    "dlg.purge.title": "永久刪除「{name}」？",
    "dlg.purge.emptyTitle": "清空垃圾桶？",
    "dlg.purge.sub": "這個動作沒有辦法復原。",
    "dlg.purge.note": "檔案會真的從 Discord 上刪掉，刪掉就拿不回來了。",
    "dlg.purge.go": "永久刪除",
    "dlg.purge.count": "垃圾桶裡的項目",

    "dlg.close": "關閉",
    "dlg.job.running": "正在永久刪除",
    "dlg.job.done": "已永久刪除",
    "dlg.job.cancelled": "已中止",
    "dlg.job.failed": "刪除中途失敗",
    "dlg.job.sub": "會一個一個刪，需要一點時間。",
    "dlg.job.current": "正在處理「{name}」",
    "dlg.job.entries": "項目",
    "dlg.job.attachments": "檔案內容",
    // 這句在四種狀態下都要出現。只在中止之後才說，會讀起來像「正常跑完
    // 反而是可以還原的那一種」。
    "dlg.job.irreversible": "已經刪掉的拿不回來。中止只會停下還沒刪的部分。",
    "dlg.job.stop": "中止",
    "dlg.job.stopping": "正在停下…",
    "dlg.job.busy": "這個硬碟上已經有一個刪除工作在進行了。",

    "dlg.conflict.title": "目的地已經有同名的項目",
    "dlg.conflict.sub": "還原「{name}」到 {dir}",
    "dlg.conflict.replace": "取代目的地的項目",
    "dlg.conflict.replaceNote":
      "目前那一份會被移到垃圾桶，不會直接消失，之後仍可還原。",
    "dlg.conflict.skip": "略過這個項目",
    "dlg.conflict.skipNote": "什麼都不做，被刪除的那一份繼續留在垃圾桶裡。",
    "dlg.conflict.both": "兩個都保留",
    "dlg.conflict.bothNote": "還原成「{name}」。",
    "dlg.conflict.inTrash": "垃圾桶裡的",
    "dlg.conflict.alreadyThere": "目前在這裡的",

    "dlg.sessions.title": "目前有 {n} 個連線登入這個帳號",
    "dlg.sessions.body":
      "同一個帳號可以在多台裝置同時登入，看到的是同一份檔案。每台裝置各自計時，不會互相延長。",
    "dlg.sessions.revoke": "登出其他所有連線",
    "dlg.sessions.revokeNote": "只會把其他裝置登出，不會把你自己登出。",
    "dlg.sessions.revoked": "已登出 {n} 個其他連線",
    "dlg.sessions.alone": "目前只有你這一個連線。",

    "expired.title": "連線已到期",
    "expired.body":
      "為了安全，閒置太久就會自動登出，檔案也跟著鎖回去。重新登入就可以繼續，已經上傳的檔案不受影響。",
    "expired.again": "重新登入",

    "integrity.title": "檔案內容對不上",
    "integrity.body":
      "「{path}」和存進來的時候不一樣，所以伺服器沒有把它交出來。這不是網路問題，也不會自己好——通常代表有人在沒有密碼的情況下動了後面的資料。",
    // 詳細資訊面板那張小卡拿不到路徑（`VerifyNote` 只收到 failed），
    // 以前是借用上面那條並把 {path} 傳空字串，畫面上就多出一對空引號。
    "integrity.bodyShort":
      "這個檔案和存進來的時候不一樣，所以下載會被擋下來。這不是網路問題，也不會自己好。",
    "integrity.ack": "我知道了",
    "integrity.log": "這次登入發現的問題（{n}）",

    "error.title": "操作失敗",
    "error.conflict": "目的地已經有同名的項目。",
    "error.notFound": "找不到這個項目，可能已經被別的連線移走了。",
    "error.rateLimited": "伺服器暫時忙不過來，稍後再試。",
    "error.dismiss": "關閉",

    "upload.failed.title": "上傳沒有完成",
    "upload.failed.short": "沒有完成",
    "upload.failed.reclaimed":
      "已經送出去的部分都清乾淨了。這個檔案不存在，直接重試就好。",
    "upload.failed.orphaned":
      "有一部分留在 Discord 上清不掉。這個檔案不存在，清單上也不會看到它。",
    "upload.failed.uploaded": "已送出",
    "upload.failed.released": "已清除",
    "upload.failed.orphans": "清不掉",
    "upload.failed.note":
      "這一項要人工處理，按重試沒有用——重試只會再傳一份，不會把上一份清掉。",
    "upload.failed.stale":
      "這個檔案會出現在清單上、大小看起來也正常，但是打不開。",
    "upload.failed.staleNote":
      "先把清單上那個項目刪掉，再重新上傳。直接重試會在一個打不開的項目旁邊多放一份。",

    "act.logout": "登出",
    "act.language": "語言",
    "act.copyPath": "複製路徑",
    "act.copied": "已複製",
    "toast.created": "已建立「{name}」",
    "toast.renamed": "已改名為「{name}」",
    "toast.trashed": "已移到垃圾桶",
    "toast.restored": "已還原到 {path}",
    "toast.skipped": "已略過",
    "toast.uploaded": "已上傳「{name}」",

    "tooSmall.title": "視窗太小",
    "tooSmall.body": "請把視窗放大到 1024 × 640 以上。",
  },

  en: {
    "app.name": "Discord Drive",
    "app.tagline": "Your files can only be opened once you sign in.",

    "nav.back": "Back",
    "nav.forward": "Forward",
    "nav.up": "Up",
    "nav.refresh": "Refresh",
    "nav.search": "Search the drive",
    "nav.newFolder": "New folder",
    "nav.upload": "Upload",
    "nav.list": "List",
    "nav.grid": "Grid",

    "side.myDrive": "MY DRIVE",
    "side.system": "SYSTEM",
    "side.trash": "Trash",

    "col.name": "Name",
    "col.size": "Size",
    "col.modified": "Modified",
    "col.location": "Location",
    "col.kind": "Kind",
    "col.integrity": "Check",
    "col.remaining": "Time left",

    "empty.title": "Nothing here yet",
    "empty.hint": "Drag files onto this window, or use Upload.",
    "empty.explainTitle": "How your files are protected",
    "empty.explainBody":
      "Files are encrypted before they are stored on Discord, and the key never leaves the server. Anyone who gets at the raw files on Discord cannot read them, and anything that has been tampered with is refused when you download it.",

    "login.username": "Username",
    "login.password": "Password",
    "login.passwordNote":
      "This password is the key to every file here. Forget it and the files can never be opened again — nobody can recover them for you.",
    "login.ttl": "How long should this session last",
    "login.ttlNote":
      "The server allows {idle} idle and {absolute} in total. You can ask for less, but not for more.",
    "login.submit": "Sign in",
    "login.working": "Checking…",
    "login.max": " (max)",
    "login.serverDown.title": "Cannot reach the server",
    "login.serverDown.body":
      "If it keeps failing, the server is probably restarting. Give it a moment and try again.",

    "login.err.401.title": "Wrong username or password",
    "login.err.401.body":
      "Check both and try again. The server will not say which one was wrong.",
    "login.err.429.title": "Temporarily locked out",
    "login.err.429.body":
      "Too many failed attempts from this device. Try again in {retry}s. Your account itself is not locked.",
    "login.err.503.title": "The server is busy",
    "login.err.503.body":
      "Too many people signing in at once. Wait a moment and try again.",
    "login.err.other.title": "Sign-in failed",

    "status.items": "{n} items",
    "status.selected": "{n} selected",
    "status.trash": "Trash · {n} items · {size}",
    "status.search": "{n} results",
    "status.searchTruncated": "{n} results (more exist, truncated)",
    "status.verified": "Listing checked",
    "status.integrity": "{n} with a problem",
    "status.idle": "idle",
    "status.absolute": "total",
    "status.connections": "{n} connections",
    "status.offline": "Lost contact with the server",

    "detail.title": "Details",
    "detail.none": "Nothing selected",
    "detail.download": "Download",
    "detail.rename": "Rename",
    "detail.delete": "Delete",
    "detail.kind": "Kind",
    "detail.size": "Size",
    "detail.chunks": "Pieces",
    "detail.modified": "Modified",
    "detail.permissions": "Permissions",
    "detail.path": "Path",
    "detail.multi": "{n} items selected",
    "detail.totalSize": "Total size",
    "detail.moveToTrash": "Move to trash",
    "detail.unverified": "Not checked yet",
    // Also the tooltip on the shield in the listing and on the status bar
    // pill, so it has to stand on its own without the text beside it.
    "detail.unverifiedNote":
      "What is in this folder has been checked, but the items themselves have not — downloading one is what actually verifies it.",
    "detail.verified": "Checked",
    "detail.verifiedNote":
      "This file passed its check when it was read: the contents and the name are exactly what was stored.",

    "trash.hint":
      "Anything here can be restored to where it was. It is only really deleted, and the space only freed, once the retention period ends.",
    "trash.empty": "Empty trash",
    "trash.restore": "Restore",
    "trash.purge": "Delete for ever",
    "trash.nothing": "The trash is empty",
    "trash.expiresIn": "deleted in {n}d",
    "trash.expiresTomorrow": "deleted tomorrow",
    "trash.expiresDue": "deleted shortly",
    "trash.retention": "kept for {n} days",

    "search.placeholder": "Part of a filename",
    "search.title": "Search the drive",
    "search.hint": "Matches file names, not what is inside the files.",
    "search.truncated": "Too many results; showing the first {n}. Try a longer query.",
    "search.none": "Nothing matched",
    "search.scanned": "{n} items searched",
    "search.reveal": "Show in folder",

    "transfer.title": "Transfers",
    "transfer.uploading": "Uploading",
    "transfer.done": "done",
    "transfer.failed": "failed",
    "transfer.cancelled": "cancelled",
    "transfer.cancel": "Cancel",
    "transfer.clear": "Clear finished",
    "transfer.note":
      "The bar shows how much has been sent. The server still needs a moment to store it before it appears in the listing.",

    "dlg.cancel": "Cancel",
    "dlg.newFolder": "New folder name",
    "dlg.create": "Create",
    "dlg.rename": "Rename",
    "dlg.renameTo": "New name",
    "dlg.confirmRename": "Rename",
    "dlg.renameNote":
      "Renaming never overwrites. A name already taken at the destination is refused.",

    "dlg.trash.title": "Move to the trash?",
    "dlg.trash.one": "“{name}”",
    "dlg.trash.many": "{n} items",
    "dlg.trash.note":
      "This can be undone. It is only really deleted once the retention period ends.",
    "dlg.trash.go": "Move to trash",
    "dlg.trash.recursive":
      "This folder is not empty; all of it goes to the trash.",

    "dlg.purge.title": "Delete “{name}” for ever?",
    "dlg.purge.emptyTitle": "Empty the trash?",
    "dlg.purge.sub": "There is no undo for this.",
    "dlg.purge.note":
      "The files are really deleted from Discord. Once they are gone, they are gone.",
    "dlg.purge.go": "Delete for ever",
    "dlg.purge.count": "Items in the trash",

    "dlg.close": "Close",
    "dlg.job.running": "Deleting",
    "dlg.job.done": "Deleted",
    "dlg.job.cancelled": "Stopped",
    "dlg.job.failed": "Stopped by an error",
    "dlg.job.sub": "Deleting one item at a time; this takes a while.",
    "dlg.job.current": "Working on “{name}”",
    "dlg.job.entries": "Items",
    "dlg.job.attachments": "Stored pieces",
    // Shown in all four states. Saying it only after a cancellation would
    // read as though finishing normally were the reversible option.
    "dlg.job.irreversible":
      "What has already been deleted does not come back. Stopping only cancels what is left.",
    "dlg.job.stop": "Stop",
    "dlg.job.stopping": "Stopping…",
    "dlg.job.busy": "There is already a deletion running on this drive.",

    "dlg.conflict.title": "There is already something with that name there",
    "dlg.conflict.sub": "Restoring “{name}” into {dir}",
    "dlg.conflict.replace": "Replace what is at the destination",
    "dlg.conflict.replaceNote":
      "The copy sitting there moves to the trash instead of vanishing, so it can still be restored.",
    "dlg.conflict.skip": "Skip this one",
    "dlg.conflict.skipNote":
      "Nothing happens; the deleted copy stays in the trash.",
    "dlg.conflict.both": "Keep both",
    "dlg.conflict.bothNote": "Restored as “{name}”.",
    "dlg.conflict.inTrash": "IN THE TRASH",
    "dlg.conflict.alreadyThere": "ALREADY THERE",

    "dlg.sessions.title": "{n} connections are signed into this account",
    "dlg.sessions.body":
      "This account can be signed in from several devices at once, all seeing the same files. Each one runs its own timer and none of them extends another.",
    "dlg.sessions.revoke": "Sign out every other connection",
    "dlg.sessions.revokeNote":
      "Signs out the other devices only; you stay signed in.",
    "dlg.sessions.revoked": "Signed out {n} other connections",
    "dlg.sessions.alone": "Yours is the only connection.",

    "expired.title": "The session is over",
    "expired.body":
      "For safety the session ends on its own, and the files lock again with it. Sign in to carry on — nothing you have already uploaded is affected.",
    "expired.again": "Sign in again",

    "integrity.title": "This file does not match",
    "integrity.body":
      "“{path}” is not what was stored, so the server did not hand it over. This is not a network glitch and it will not clear up on its own — it usually means somebody changed the stored data without the password.",
    // The details-pane card has no path to show (`VerifyNote` only receives
    // `failed`); it used to borrow the line above with {path} set to "",
    // which rendered a pair of empty quotes.
    "integrity.bodyShort":
      "This file is not what was stored, so downloading it is refused. This is not a network glitch and it will not clear up on its own.",
    "integrity.ack": "Got it",
    "integrity.log": "Problems found this session ({n})",

    "error.title": "That did not work",
    "error.conflict": "Something with that name is already there.",
    "error.notFound":
      "That is gone — another connection may have moved or deleted it.",
    "error.rateLimited": "The server is busy; try again shortly.",
    "error.dismiss": "Dismiss",

    "upload.failed.title": "The upload did not finish",
    "upload.failed.short": "Did not finish",
    "upload.failed.reclaimed":
      "Everything that got through was cleaned up again. The file is not there; trying again is fine.",
    "upload.failed.orphaned":
      "Part of it was left on Discord and could not be removed. The file is not there and will not appear in the listing.",
    "upload.failed.uploaded": "Sent",
    "upload.failed.released": "Cleaned up",
    "upload.failed.orphans": "Could not be cleaned up",
    "upload.failed.note":
      "This one needs sorting out by hand rather than a retry — retrying sends a second copy, it does not clean up the first.",
    "upload.failed.stale":
      "This file will show up in the listing at what looks like a normal size, but it cannot be opened.",
    "upload.failed.staleNote":
      "Delete that entry from the listing first, then upload again. Retrying straight away leaves a second copy next to one that cannot be opened.",

    "act.logout": "Sign out",
    "act.language": "Language",
    "act.copyPath": "Copy path",
    "act.copied": "Copied",
    "toast.created": "Created “{name}”",
    "toast.renamed": "Renamed to “{name}”",
    "toast.trashed": "Moved to the trash",
    "toast.restored": "Restored to {path}",
    "toast.skipped": "Skipped",
    "toast.uploaded": "Uploaded “{name}”",

    "tooSmall.title": "The window is too small",
    "tooSmall.body": "Please make the window at least 1024 × 640.",
  },
};

export const LANGUAGES = ["zh", "en"];

export function translate(lang, key, vars) {
  const table = DICT[lang] || DICT.zh;
  // Falling back to the key rather than to the other language: a missing
  // string then shows as `dlg.purge.title` in the interface, which is
  // obviously wrong. Falling back to Chinese would look deliberate and ship.
  const template = table[key] ?? DICT.zh[key] ?? key;
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : whole,
  );
}

export function useTranslate(lang) {
  return useCallback((key, vars) => translate(lang, key, vars), [lang]);
}
