import { useEffect, useState } from 'react'
import Chat from './views/Chat'
import Admin from './views/Admin'

type Route = 'chat' | 'admin'

function routeFromHash(): Route {
  return window.location.hash === '#/admin' ? 'admin' : 'chat'
}

export default function App() {
  const [route, setRoute] = useState<Route>(routeFromHash)

  useEffect(() => {
    const onHashChange = () => setRoute(routeFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  return (
    <div className="app">
      <header className="masthead">
        <div className="brand">
          Waypoint Supply <small>refund support</small>
        </div>
        <nav>
          <a href="#/" className={route === 'chat' ? 'active' : ''}>
            Support
          </a>
          <a href="#/admin" className={route === 'admin' ? 'active' : ''}>
            Operations
          </a>
        </nav>
      </header>
      {route === 'chat' ? <Chat /> : <Admin />}
    </div>
  )
}
