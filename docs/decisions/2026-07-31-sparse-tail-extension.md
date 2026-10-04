# 2026-07-31 · 檔案擴張採「稀疏尾端」，不實際補零

**決策**：檔案擴張採「稀疏尾端」，不實際補零

**依據**：
（2026-07-31）。`size` 可以大於所有 chunk 長度總和，
中間那段讀回零、不佔 Discord 空間。**洞只會在尾端**；寫入落在 chunk 之後仍然實際補零。
理由是效能：真的補零會讓「先設定大小再上傳」的客戶端每個 SFTP 封包都落在檔案中間、
各重傳一整塊 chunk，9MB 的 chunk 會被重傳數百次。由
`tests/test_truncate.py::test_presetting_the_size_does_not_change_the_upload_count` 釘住。

**反悔成本**：搬入時原文未單獨記錄（2026-10-04 自 `ROADMAP.md`〈已拍板的長期決策〉搬入）。
