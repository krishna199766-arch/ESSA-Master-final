import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './styles.css'

// This app is never a frame's content. It PUTS the shop in a frame; nothing puts
// it in one.
//
// So finding ourselves inside one means a request for a POS screen came back as
// the warehouse instead — a routing rule that missed, a login redirect that lost
// its prefix. Rendering anyway is what turned that into three stacked copies of
// the header: the app draws its own POS frame, which asks for a POS screen, which
// comes back as the app, which draws another frame. The browser gives up after a
// few and leaves a page that looks like a display bug rather than a wrong answer
// from the server.
//
// Refusing to mount stops it at the first one and says what actually happened,
// which is the difference between a fault somebody can act on and a fault that
// looks like CSS.
const framed = (() => {
  try {
    return window.top !== window.self
  } catch {
    // cross-origin parent: reading window.top throws, and a parent we are not
    // allowed to look at is certainly not us
    return true
  }
})()

if (framed) {
  document.getElementById('root').innerHTML = `
    <div style="font:14px/1.65 system-ui,sans-serif;color:#17221D;padding:28px;max-width:640px">
      <h2 style="margin:0 0 10px;font-size:17px">This is the warehouse app, in a frame</h2>
      <p style="margin:0 0 12px">
        Something asked for a <b>POS</b> page and got this instead, so the shop is
        not what loaded here. It is a routing fault on the server, not a problem
        with this screen.
      </p>
      <p style="margin:0 0 12px">
        The warehouse app does not run inside a frame — it is the thing that opens
        one — so it has stopped rather than loading itself over and over.
      </p>
      <p style="margin:0">
        <a href="/" target="_top" style="color:#0B3D2E;font-weight:600">Open the warehouse ↗</a>
      </p>
    </div>`
} else {
  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
}
