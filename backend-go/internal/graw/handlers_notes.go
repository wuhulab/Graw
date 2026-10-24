package graw

import (
	"net/http"
)

// 备忘录内容上限（256KB），防止无限写入撑爆 data 分区。
const maxNoteBytes = 256 * 1024

type noteData struct {
	Content string `json:"content"`
}

func registerNotesRoutes(a *App) {
	a.Get("/api/notes", protected(), func(w http.ResponseWriter, r *http.Request) error {
		var data noteData
		if err := ReadJSON(DataPath("notes.json"), &data); err != nil && !IsNotExist(err) {
			data = noteData{Content: ""}
		}
		return WriteJSON(w, 200, data)
	})
	a.Post("/api/notes", adminOnly(), func(w http.ResponseWriter, r *http.Request) error {
		u := CurrentUser(r)
		var req noteData
		if err := DecodeJSON(r, &req); err != nil {
			return err
		}
		if len([]byte(req.Content)) > maxNoteBytes {
			return NewHTTPError(413, "备忘录内容过大（最大 256KB），请精简后保存")
		}
		if err := WriteJSONFile(DataPath("notes.json"), noteData{Content: req.Content}); err != nil {
			return err
		}
		AuditRecord("更新备忘录", u.Username(), ClientIP(r), "")
		return WriteJSON(w, 200, map[string]any{"ok": true})
	})
}
