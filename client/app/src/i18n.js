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

import { useCallback } from "react";

const DICT = {
  zh: {
    "app.name": "Discord Drive",
    "app.tagline": "登入會把主金鑰解到伺服器行程的記憶體裡。",

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
    "col.integrity": "完整性",
    "col.remaining": "剩餘保留",

    "empty.title": "這裡還沒有東西",
    "empty.hint": "把檔案拖到這個視窗，或用上面的「上傳」。",
    "empty.explainTitle": "上傳的時候會發生什麼",
    "empty.explainBody":
      "每個檔案切成 9 MiB 分塊，每塊各自以 AES-256-CTR 加密（自己的 nonce）、以 HMAC-SHA256 認證。認證標籤留在 MongoDB，不會上 Discord——所以掌握 Discord 那一側的人既讀不出分塊，也偽造不出來。標籤同時涵蓋檔名與它所在的目錄，直接在資料庫裡改名或搬移，下一次讀取就會被抓到。",

    "login.username": "使用者名稱",
    "login.password": "密碼",
    "login.passwordNote":
      "這組密碼包裝著所有檔案的加密金鑰。弄丟它是弄丟檔案，不只是弄丟登入。",
    "login.ttl": "這次連線要活多久",
    "login.ttlNote":
      "伺服器上限是閒置 {idle}、絕對 {absolute}。往短調會被接受，往長調會被夾回來——client 能延長的期限，等於是偷到 cookie 的人在控制。",
    "login.submit": "登入",
    "login.working": "驗證中…",
    "login.max": "（上限）",
    "login.serverDown.title": "連不上伺服器",
    "login.serverDown.body":
      "這個畫面是伺服器吐出來的，所以你看得到它就代表連得上。如果動作一直失敗，多半是後端正在重啟。",

    "login.err.401.title": "401 · 使用者名稱或密碼錯誤",
    "login.err.401.body":
      "伺服器不會說是哪一半錯了。帳號不存在時照樣跑一次假驗證，時間看起來一樣。",
    "login.err.429.title": "429 · 這個來源暫時被鎖住",
    "login.err.429.body":
      "鎖定的鍵是（來源位址＋裝置 id），永遠不鎖帳號——鎖帳號等於誰打錯幾次就能把擁有者關在門外。{retry}秒後可再試。",
    "login.err.503.title": "503 · 登入排隊已滿",
    "login.err.503.body":
      "一次登入要跑兩輪 Argon2id，各 64 MiB。超過佇列深度直接回 503 而不是繼續排——無上限的佇列是同一個故障多繞幾步。稍候再試。",
    "login.err.other.title": "登入失敗",

    "status.items": "{n} 個項目",
    "status.selected": "已選 {n}",
    "status.trash": "垃圾桶 · {n} 個項目 · {size}",
    "status.search": "{n} 個結果",
    "status.searchTruncated": "{n} 個結果（還有更多，已截斷）",
    "status.verified": "列目錄的子項集合已驗證",
    "status.integrity": "{n} 項驗證失敗",
    "status.idle": "閒置",
    "status.absolute": "絕對",
    "status.connections": "{n} 個連線",
    "status.offline": "與伺服器失去聯絡",

    "detail.title": "詳細資訊",
    "detail.none": "沒有選取項目",
    "detail.download": "下載",
    "detail.rename": "重新命名",
    "detail.delete": "刪除",
    "detail.kind": "種類",
    "detail.size": "大小",
    "detail.chunks": "分塊（9 MiB）",
    "detail.modified": "修改時間",
    "detail.permissions": "權限",
    "detail.path": "路徑",
    "detail.multi": "選取了 {n} 個項目",
    "detail.totalSize": "總大小",
    "detail.moveToTrash": "移到垃圾桶",
    "detail.unverified": "清單未逐項驗證",
    "detail.unverifiedNote":
      "列目錄只驗子項集合（誰在裡面），不驗每個子項自己的標籤。所以這裡的大小是未經驗證的——開啟或下載時才會真的檢查。",
    "detail.verified": "已驗證",
    "detail.verifiedNote":
      "這個節點的標籤在讀取時通過了：每塊的 HMAC、有序的分塊清單、檔名與所在目錄都涵蓋在內。權限位與時間戳刻意不在裡面。",

    "trash.hint":
      "垃圾桶裡的項目原地不動，還原時沿 parent_id 走回去。保留期滿由背景清理，屆時才會真的釋放 Discord 上的附件。",
    "trash.empty": "清空垃圾桶",
    "trash.restore": "還原",
    "trash.purge": "永久刪除",
    "trash.nothing": "垃圾桶是空的",
    "trash.expiresIn": "{n} 天後永久刪除",
    "trash.expiresTomorrow": "明天永久刪除",
    "trash.expiresDue": "下次清理時刪除",
    "trash.retention": "保留 {n} 天",

    "search.placeholder": "輸入檔名的一部分",
    "search.title": "搜尋整個硬碟",
    "search.hint":
      "比對檔名，不比對內容——內容搜尋要把每一個分塊解密。掃描時每一層目錄都會驗證子項集合。",
    "search.truncated":
      "結果太多，只顯示前 {n} 筆。把關鍵字打長一點。",
    "search.none": "沒有符合的項目",
    "search.scanned": "掃過 {n} 個節點",
    "search.reveal": "在資料夾中顯示",

    "transfer.title": "傳輸",
    "transfer.uploading": "上傳中",
    "transfer.done": "完成",
    "transfer.failed": "失敗",
    "transfer.cancelled": "已取消",
    "transfer.cancel": "取消",
    "transfer.clear": "清除已完成",
    "transfer.note":
      "進度是瀏覽器送出的位元組數，不是伺服器切到第幾塊——分塊是伺服器那側做的。",

    "dlg.cancel": "取消",
    "dlg.newFolder": "新資料夾名稱",
    "dlg.create": "建立",
    "dlg.rename": "重新命名",
    "dlg.renameTo": "新名稱",
    "dlg.confirmRename": "改名",
    "dlg.renameNote": "改名不會覆蓋既有檔案；目的地已存在就會被拒絕。",

    "dlg.trash.title": "移到垃圾桶？",
    "dlg.trash.one": "「{name}」",
    "dlg.trash.many": "{n} 個項目",
    "dlg.trash.note":
      "可以還原。附件要等保留期滿才會真的從 Discord 釋放。",
    "dlg.trash.go": "移到垃圾桶",
    "dlg.trash.recursive":
      "這個資料夾裡還有東西，會整棵移進垃圾桶。",

    "dlg.purge.title": "永久刪除「{name}」？",
    "dlg.purge.emptyTitle": "清空垃圾桶？",
    "dlg.purge.sub": "這個動作沒有辦法復原。",
    "dlg.purge.note":
      "附件會逐一從 Discord 刪除。已經刪掉的不會回來。",
    "dlg.purge.go": "永久刪除",
    "dlg.purge.count": "垃圾桶裡的項目",

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
      "這個帳號可以同時被多個裝置登入，共用同一棵樹與同一把金鑰——這就是目前分享這個硬碟的方式。每一個連線各自持有自己的期限，不會互相延長。",
    "dlg.sessions.revoke": "登出其他所有連線",
    "dlg.sessions.revokeNote":
      "只會結束其他連線，不會把你自己登出。被結束的那一側會看到「連線已到期」。",
    "dlg.sessions.revoked": "已登出 {n} 個其他連線",
    "dlg.sessions.alone": "目前只有你這一個連線。",

    "expired.title": "連線已到期",
    "expired.body":
      "主金鑰已經從伺服器行程的記憶體釋放。在你重新登入之前，什麼都讀不出來——這是期限的目的，不是它的副作用。已經上傳的檔案不受影響。",
    "expired.again": "重新登入",

    "integrity.title": "完整性驗證失敗",
    "integrity.body":
      "「{path}」的認證標籤與內容對不上。伺服器沒有回傳任何位元組——回傳了就等於讓這道檢查失去意義。這代表有人在沒有金鑰的情況下改動了資料庫或 Discord 上的內容，不是網路問題，也不是暫時性錯誤。",
    "integrity.ack": "我知道了，記錄下來",
    "integrity.log": "本次連線的完整性事件（{n}）",

    "error.title": "操作失敗",
    "error.conflict": "目的地已經有同名的項目。",
    "error.notFound": "找不到這個項目，可能已經被別的連線移走了。",
    "error.rateLimited": "伺服器暫時忙不過來，稍後再試。",
    "error.dismiss": "關閉",

    "act.logout": "登出",
    "act.language": "語言",
    "act.copyPath": "複製路徑",
    "act.copied": "已複製",
    "toast.created": "已建立「{name}」",
    "toast.renamed": "已改名為「{name}」",
    "toast.trashed": "已移到垃圾桶",
    "toast.restored": "已還原到 {path}",
    "toast.skipped": "已略過",
    "toast.purged": "已永久刪除",
    "toast.emptied": "垃圾桶已清空",
    "toast.uploaded": "已上傳「{name}」",

    "tooSmall.title": "視窗太小",
    "tooSmall.body":
      "這個版面保證在 1024 × 640 以上。再小下去，側欄樹、清單和詳細資訊沒辦法同時誠實呈現。",
  },

  en: {
    "app.name": "Discord Drive",
    "app.tagline": "Signing in unwraps the master key into the server's memory.",

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
    "col.integrity": "Integrity",
    "col.remaining": "Retention",

    "empty.title": "Nothing here yet",
    "empty.hint": "Drag files onto this window, or use Upload.",
    "empty.explainTitle": "What happens when you upload",
    "empty.explainBody":
      "Each file is split into 9 MiB chunks. Every chunk is encrypted with AES-256-CTR under its own nonce and authenticated with HMAC-SHA256. The tags stay in MongoDB and never go to Discord, so whoever controls the Discord side can neither read a chunk nor forge one. The tags also cover the file's name and the directory it sits in — a rename or a move done in the database is caught on the next read.",

    "login.username": "Username",
    "login.password": "Password",
    "login.passwordNote":
      "This password wraps the key every stored file is encrypted with. Losing it loses the files, not just the login.",
    "login.ttl": "How long should this session last",
    "login.ttlNote":
      "The server's ceiling is {idle} idle / {absolute} absolute. A client can ask for shorter and get it; asking for longer is clamped back — a deadline the client could extend is a deadline whoever stole the cookie controls.",
    "login.submit": "Sign in",
    "login.working": "Checking…",
    "login.max": " (max)",
    "login.serverDown.title": "Cannot reach the server",
    "login.serverDown.body":
      "This page came from the server, so seeing it means it was reachable. Repeated failures usually mean the backend is restarting.",

    "login.err.401.title": "401 · Wrong username or password",
    "login.err.401.body":
      "The server will not say which half was wrong. A missing account still runs a dummy verification, so the timing looks the same.",
    "login.err.429.title": "429 · This source is locked out",
    "login.err.429.body":
      "The lock is keyed on (source address + device id) and never on the account: locking the account means anybody who mistypes can shut the owner out. Try again in {retry}s.",
    "login.err.503.title": "503 · The sign-in queue is full",
    "login.err.503.body":
      "One sign-in runs two rounds of Argon2id at 64 MiB each. Past the queue depth the server refuses rather than queueing further — an unbounded queue is the same failure with extra steps.",
    "login.err.other.title": "Sign-in failed",

    "status.items": "{n} items",
    "status.selected": "{n} selected",
    "status.trash": "Trash · {n} items · {size}",
    "status.search": "{n} results",
    "status.searchTruncated": "{n} results (more exist, truncated)",
    "status.verified": "Directory membership verified",
    "status.integrity": "{n} failed verification",
    "status.idle": "idle",
    "status.absolute": "absolute",
    "status.connections": "{n} connections",
    "status.offline": "Lost contact with the server",

    "detail.title": "Details",
    "detail.none": "Nothing selected",
    "detail.download": "Download",
    "detail.rename": "Rename",
    "detail.delete": "Delete",
    "detail.kind": "Kind",
    "detail.size": "Size",
    "detail.chunks": "Chunks (9 MiB)",
    "detail.modified": "Modified",
    "detail.permissions": "Permissions",
    "detail.path": "Path",
    "detail.multi": "{n} items selected",
    "detail.totalSize": "Total size",
    "detail.moveToTrash": "Move to trash",
    "detail.unverified": "Not verified per-entry",
    "detail.unverifiedNote":
      "Listing verifies membership (who is in the directory), not each entry's own tag. The size shown here is therefore unverified — opening or downloading is what actually checks it.",
    "detail.verified": "Verified",
    "detail.verifiedNote":
      "This node's tag passed on read: per-chunk HMACs, the ordered chunk list, the filename and its parent are all covered. Permission bits and timestamps are deliberately outside it.",

    "trash.hint":
      "Items stay where they were; restoring walks parent_id back up. Discord attachments are only released once the retention period ends and the sweep runs.",
    "trash.empty": "Empty trash",
    "trash.restore": "Restore",
    "trash.purge": "Delete for ever",
    "trash.nothing": "The trash is empty",
    "trash.expiresIn": "destroyed in {n}d",
    "trash.expiresTomorrow": "destroyed tomorrow",
    "trash.expiresDue": "destroyed on the next sweep",
    "trash.retention": "{n}-day retention",

    "search.placeholder": "Part of a filename",
    "search.title": "Search the drive",
    "search.hint":
      "Names, not contents — searching contents would mean decrypting every chunk. Every directory on the way down has its membership tag checked.",
    "search.truncated": "Too many results; showing the first {n}. Try a longer query.",
    "search.none": "Nothing matched",
    "search.scanned": "{n} nodes scanned",
    "search.reveal": "Show in folder",

    "transfer.title": "Transfers",
    "transfer.uploading": "Uploading",
    "transfer.done": "done",
    "transfer.failed": "failed",
    "transfer.cancelled": "cancelled",
    "transfer.cancel": "Cancel",
    "transfer.clear": "Clear finished",
    "transfer.note":
      "Progress is bytes the browser has sent, not chunks that reached Discord — the splitting happens on the server side.",

    "dlg.cancel": "Cancel",
    "dlg.newFolder": "New folder name",
    "dlg.create": "Create",
    "dlg.rename": "Rename",
    "dlg.renameTo": "New name",
    "dlg.confirmRename": "Rename",
    "dlg.renameNote":
      "Renaming never overwrites; a name already taken at the destination is refused.",

    "dlg.trash.title": "Move to the trash?",
    "dlg.trash.one": "“{name}”",
    "dlg.trash.many": "{n} items",
    "dlg.trash.note":
      "This can be undone. Attachments are only released from Discord once the retention period ends.",
    "dlg.trash.go": "Move to trash",
    "dlg.trash.recursive":
      "This folder is not empty; the whole tree goes to the trash.",

    "dlg.purge.title": "Destroy “{name}” for ever?",
    "dlg.purge.emptyTitle": "Empty the trash?",
    "dlg.purge.sub": "There is no undo for this.",
    "dlg.purge.note":
      "Attachments are deleted from Discord one at a time. What is gone is gone.",
    "dlg.purge.go": "Destroy",
    "dlg.purge.count": "Items in the trash",

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
      "This account can be signed in from several devices at once, sharing one tree and one key — that is what sharing this drive looks like today. Each connection holds its own deadlines and none of them extends another.",
    "dlg.sessions.revoke": "Sign out every other connection",
    "dlg.sessions.revokeNote":
      "Ends the others only; it does not sign you out. The other side sees “session expired”.",
    "dlg.sessions.revoked": "Signed out {n} other connections",
    "dlg.sessions.alone": "Yours is the only connection.",

    "expired.title": "The session is over",
    "expired.body":
      "The master key has been released from the server process's memory. Nothing is readable until you sign in again — that is the point of the deadline, not a side effect of it. Files already uploaded are untouched.",
    "expired.again": "Sign in again",

    "integrity.title": "Integrity check failed",
    "integrity.body":
      "The authentication tag for “{path}” does not match the content. No bytes were returned; returning them would make the check pointless. Somebody changed MongoDB or the Discord side without the key. This is not a network error and not transient.",
    "integrity.ack": "Acknowledge and record",
    "integrity.log": "Integrity events this session ({n})",

    "error.title": "That did not work",
    "error.conflict": "Something with that name is already there.",
    "error.notFound":
      "That is gone — another connection may have moved or deleted it.",
    "error.rateLimited": "The server is busy; try again shortly.",
    "error.dismiss": "Dismiss",

    "act.logout": "Sign out",
    "act.language": "Language",
    "act.copyPath": "Copy path",
    "act.copied": "Copied",
    "toast.created": "Created “{name}”",
    "toast.renamed": "Renamed to “{name}”",
    "toast.trashed": "Moved to the trash",
    "toast.restored": "Restored to {path}",
    "toast.skipped": "Skipped",
    "toast.purged": "Destroyed",
    "toast.emptied": "The trash is empty",
    "toast.uploaded": "Uploaded “{name}”",

    "tooSmall.title": "The window is too small",
    "tooSmall.body":
      "This layout is guaranteed at 1024 × 640 and up. Below that the tree, the list and the details panel cannot all be honest at once.",
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
