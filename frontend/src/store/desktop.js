import { reactive } from 'vue'

let idCounter = 1
let zBase = 100

export const desktop = reactive({
  windows: [],
  open(type, title, data = {}) {
    const id = idCounter++
    zBase += 1
    const w = {
      id,
      type,
      title,
      x: 60 + (this.windows.length * 24) % 300,
      y: 40 + (this.windows.length * 24) % 200,
      width: data.width || 800,
      height: data.height || 520,
      active: true,
      minimized: false,
      maximized: false,
      zIndex: zBase,
      data,
    }
    this.windows.forEach(x => x.active = false)
    this.windows.push(w)
    return w
  },
  close(id) {
    const idx = this.windows.findIndex(w => w.id === id)
    if (idx >= 0) this.windows.splice(idx, 1)
  },
  activate(id) {
    zBase += 1
    this.windows.forEach(w => {
      w.active = w.id === id
      if (w.id === id) {
        w.zIndex = zBase
        w.minimized = false
      }
    })
  },
  minimize(id) {
    const w = this.windows.find(w => w.id === id)
    if (w) w.minimized = true
  },
  toggleMaximize(id) {
    const w = this.windows.find(w => w.id === id)
    if (w) w.maximized = !w.maximized
  },
})
