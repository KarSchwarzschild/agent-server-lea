import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'
import test from 'node:test'

const require = createRequire(new URL('../package.json', import.meta.url))
const vueRequire = createRequire(require.resolve('vue/package.json'))
const viteRequire = createRequire(require.resolve('vite/package.json'))
const { parse, compileScript } = vueRequire('@vue/compiler-sfc')
const { transformSync } = viteRequire('esbuild')
const { createSSRApp } = require('vue')
const { renderToString } = vueRequire('@vue/server-renderer')

// Exercise the real component and SSE parser without mounting a browser or
// contacting a model. Only the HTTP boundary and unrendered UI imports are fake.
const appPath = fileURLToPath(new URL('../src/App.vue', import.meta.url))
const apiPath = fileURLToPath(new URL('../src/api.ts', import.meta.url))
const descriptor = parse(readFileSync(appPath, 'utf8')).descriptor
const compile = source => transformSync(source, {
  loader: 'ts', format: 'cjs', target: 'node20',
}).code
const appCode = compile(compileScript(descriptor, { id: 'chat-session-test' }).content)
const apiCode = compile(readFileSync(apiPath, 'utf8'))
const encoder = new TextEncoder()
const tick = () => new Promise(resolve => setImmediate(resolve))

async function settle() {
  for (let i = 0; i < 5; i += 1) await tick()
}

async function until(predicate, description) {
  for (let i = 0; i < 100; i += 1) {
    if (predicate()) return
    await tick()
  }
  assert.fail(`Timed out waiting for ${description}`)
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function createTransport() {
  const requests = []
  const jsonGates = new Map()
  const jsonBodies = new Map()

  function stream(request) {
    const headers = deferred()
    let controller
    let opened = false
    let closed = false
    const body = new ReadableStream({ start(value) { controller = value } })
    request.respond = () => {
      opened = true
      headers.resolve(new Response(body, {
        headers: { 'Content-Type': 'text/event-stream' },
      }))
    }
    request.respondError = () => headers.resolve(Response.json({ error: 'old request failed' }, { status: 500 }))
    request.emit = (...events) => {
      if (!closed) controller.enqueue(encoder.encode(events.map(([type, data]) =>
        `event: ${type}\ndata: ${typeof data === 'string' ? data : JSON.stringify(data)}\n\n`,
      ).join('')))
    }
    request.close = () => {
      if (!closed) { closed = true; controller.close() }
    }
    // Before headers, native fetch rejects on abort. After headers, deliberately
    // allow an already buffered read to resolve: abort cannot retract callbacks
    // which have already entered the event queue.
    request.signal?.addEventListener('abort', () => {
      if (!opened) headers.reject(new DOMException('Aborted', 'AbortError'))
    }, { once: true })
    if (request.signal?.aborted) headers.reject(new DOMException('Aborted', 'AbortError'))
    return headers.promise
  }

  async function fetch(url, options = {}) {
    const path = new URL(String(url), 'http://chat.test').pathname
    const request = {
      path, method: options.method || 'GET', signal: options.signal,
      payload: options.body ? JSON.parse(options.body) : undefined,
    }
    requests.push(request)
    if (path === '/api/chat/stream' || path.startsWith('/api/chat/stream/')) return stream(request)
    if (jsonGates.has(path)) return Response.json(await jsonGates.get(path).promise)
    if (jsonBodies.has(path)) return Response.json(jsonBodies.get(path))
    if (path.endsWith('/messages')) return Response.json({ messages: [] })
    if (path.endsWith('/attachments')) return Response.json({ attachments: [] })
    if (path.startsWith('/api/chat/status/')) return Response.json({ session_id: path.split('/').at(-1), running: false })
    if (path === '/api/files/list') return Response.json({ files: [], truncated: false, store: 'test' })
    if (path === '/api/sessions') return Response.json({ sessions: [] })
    throw new Error(`Unexpected test HTTP request: ${request.method} ${path}`)
  }

  return {
    requests, fetch,
    streams: () => requests.filter(request => request.path === '/api/chat/stream'),
    deferJson(path) {
      const gate = deferred()
      jsonGates.set(path, gate)
      return body => gate.resolve(body)
    },
    setJson: (path, body) => jsonBodies.set(path, body),
    close() {
      for (const request of requests) request.close?.()
    },
  }
}

async function mountChat(t) {
  const transport = createTransport()
  const storage = new Map()
  const context = vm.createContext({
    console, AbortController, DOMException, Response, ReadableStream, TextDecoder,
    TextEncoder, URL, Blob, FormData, performance, setTimeout, clearTimeout,
    fetch: transport.fetch,
    localStorage: {
      getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: key => storage.delete(key),
    },
  })
  function evaluate(code, filename) {
    const module = { exports: {} }
    const localRequire = id => {
      if (id === './api') return api
      if (id.endsWith('.css') || id === '@vicons/ionicons5' || id === 'naive-ui') return {}
      return require(id)
    }
    const factory = vm.runInContext(`(function(require, module, exports) { ${code}\n})`, context, { filename })
    factory(localRequire, module, module.exports)
    return module.exports
  }
  const api = evaluate(apiCode, apiPath)
  const component = evaluate(appCode, appPath).default
  let state
  await renderToString(createSSRApp({
    ...component,
    setup(props, setupContext) {
      state = component.setup(props, setupContext)
      return state
    },
    render: () => null,
  }))
  t.after(async () => {
    state.detachCurrentStream()
    transport.close()
    await settle()
  })
  return { state, transport }
}

async function startMessage(chat, message) {
  const index = chat.transport.streams().length
  const completion = chat.state.sendChatMessage(message)
  await until(() => chat.transport.streams().length > index, `request for ${message}`)
  return { request: chat.transport.streams()[index], completion }
}

async function startB(chat) {
  const pending = await startMessage(chat, 'question B')
  pending.request.respond()
  pending.request.emit(['session', { session_id: 'B' }], ['delta', 'answer B in progress'])
  await pending.completion
  await settle()
  return pending.request
}

function assertBIsRunning(state) {
  assert.equal(state.currentSessionId.value, 'B', 'late A work must not select A over B')
  assert.equal(state.streamRunning.value, true, 'late A work must not unlock B while B is generating')
  assert.deepEqual(Array.from(state.messages.value, message => [message.role, message.text]), [
    ['user', 'question B'], ['assistant', 'answer B in progress'],
  ])
}

test('late A headers and SSE events cannot replace B or change its next request session', async t => {
  const chat = await mountChat(t)
  const a = await startMessage(chat, 'question A')
  chat.state.newConversation()
  const b = await startB(chat)

  a.request.respond()
  a.request.emit(
    ['session', { session_id: 'A' }], ['delta', 'late answer A'],
    ['error', 'late A error'], ['done', {}],
  )
  await a.completion
  await settle()
  assertBIsRunning(chat.state)
  assert.equal(a.request.signal.aborted, true, 'release the detached A connection when its headers arrive')
  assert.equal(b.signal.aborted, false, 'the late A handle must not replace or abort B')

  b.emit(['result', { text: 'complete answer B' }], ['done', {}])
  await settle()
  const followup = await startMessage(chat, 'followup B')
  assert.equal(followup.request.payload.session_id, 'B')
  followup.request.respond()
  followup.request.emit(['delta', 'followup answer B'], ['done', {}])
  await followup.completion
})

test('late A HTTP failure cannot finish the B stream', async t => {
  const chat = await mountChat(t)
  const a = await startMessage(chat, 'question A')
  chat.state.newConversation()
  await startB(chat)
  a.request.respondError()
  await a.completion
  await settle()
  assertBIsRunning(chat.state)
})

test('buffered A events arriving after its headers were detached cannot affect B', async t => {
  const chat = await mountChat(t)
  const a = await startMessage(chat, 'question A')
  a.request.respond()
  a.request.emit(['session', { session_id: 'A' }], ['delta', 'partial answer A'])
  await a.completion
  await settle()
  chat.state.newConversation()
  await startB(chat)

  a.request.emit(
    ['session', { session_id: 'A' }], ['delta', 'buffered A'],
    ['error', 'buffered A error'], ['done', {}],
  )
  await settle()
  assertBIsRunning(chat.state)
})

test('a delayed history response cannot replace the newly created B conversation', async t => {
  const chat = await mountChat(t)
  const releaseHistory = chat.transport.deferJson('/api/sessions/A/messages')
  const opening = chat.state.openSession('A')
  await until(() => chat.transport.requests.some(request => request.path === '/api/sessions/A/messages'), 'A history request')
  chat.state.newConversation()
  await startB(chat)

  releaseHistory({ messages: [{ id: 1, role: 'user', content: 'old A question', extra: {} }] })
  await opening
  await settle()
  assertBIsRunning(chat.state)
})

test('a delayed running status for A cannot append a resumed A turn to B', async t => {
  const chat = await mountChat(t)
  const releaseStatus = chat.transport.deferJson('/api/chat/status/A')
  const opening = chat.state.openSession('A')
  await until(() => chat.transport.requests.some(request => request.path === '/api/chat/status/A'), 'A status request')
  chat.state.newConversation()
  await startB(chat)

  releaseStatus({ session_id: 'A', running: true })
  await settle()
  const resume = chat.transport.requests.find(request => request.path === '/api/chat/stream/A')
  resume?.respond()
  await opening
  await settle()
  assertBIsRunning(chat.state)
})

test('EOF without done reloads the completed history when the turn is no longer running', async t => {
  const chat = await mountChat(t)
  const b = await startB(chat)
  chat.transport.setJson('/api/sessions/B/messages', {
    messages: [
      { id: 11, role: 'user', content: 'question B', created_at: '2026-10-30T01:00:00Z', extra: {} },
      { id: 12, role: 'assistant', content: 'complete answer B from history', created_at: '2026-10-30T01:00:01Z', extra: {} },
    ],
  })

  b.close()
  await settle()
  assert.deepEqual(Array.from(chat.state.messages.value, message => [message.role, message.text]), [
    ['user', 'question B'], ['assistant', 'complete answer B from history'],
  ])
  assert.equal(chat.state.streamRunning.value, false)
  assert.equal(chat.state.currentSessionId.value, 'B')
})

test('an A disconnect status arriving after switching to B cannot reopen A', async t => {
  const chat = await mountChat(t)
  const a = await startMessage(chat, 'question A')
  a.request.respond()
  a.request.emit(['session', { session_id: 'A' }], ['delta', 'partial answer A'])
  await a.completion
  await settle()
  const releaseStatus = chat.transport.deferJson('/api/chat/status/A')
  a.request.close()
  await until(() => chat.transport.requests.some(request => request.path === '/api/chat/status/A'), 'A disconnect status')
  chat.state.newConversation()
  await startB(chat)

  releaseStatus({ session_id: 'A', running: false })
  await settle()
  assertBIsRunning(chat.state)
})

test('an A stop response arriving after switching to B cannot abort or finish B', async t => {
  const chat = await mountChat(t)
  const a = await startMessage(chat, 'question A')
  a.request.respond()
  a.request.emit(['session', { session_id: 'A' }], ['delta', 'partial answer A'])
  await a.completion
  await settle()
  const releaseStop = chat.transport.deferJson('/api/chat/stop/A')
  const stopping = chat.state.stopStream()
  await until(() => chat.transport.requests.some(request => request.path === '/api/chat/stop/A'), 'A stop request')
  assert.equal(chat.transport.requests.find(request => request.path === '/api/chat/stop/A').method, 'POST')
  chat.state.newConversation()
  const b = await startB(chat)

  releaseStop({ session_id: 'A', stopped: true })
  await stopping
  await settle()
  assertBIsRunning(chat.state)
  assert.equal(b.signal.aborted, false, 'stopping A must keep the B connection open')
  b.emit(['delta', '|still streaming'])
  await settle()
  assert.equal(chat.state.messages.value.at(-1).text, 'answer B in progress|still streaming')
})

test('reopening A and B displays both stored answers and the next B request keeps its session', async t => {
  const chat = await mountChat(t)
  chat.transport.setJson('/api/sessions/A/messages', {
    messages: [
      { id: 21, role: 'user', content: 'stored question A', created_at: '2026-10-30T01:00:00Z', extra: {} },
      { id: 22, role: 'assistant', content: 'stored answer A', created_at: '2026-10-30T01:00:01Z', extra: {} },
    ],
  })
  chat.transport.setJson('/api/sessions/B/messages', {
    messages: [
      { id: 31, role: 'user', content: 'stored question B', created_at: '2026-10-30T01:01:00Z', extra: {} },
      { id: 32, role: 'assistant', content: 'stored answer B', created_at: '2026-10-30T01:01:01Z', extra: {} },
    ],
  })

  await chat.state.openSession('A')
  assert.deepEqual(Array.from(chat.state.messages.value, message => [message.role, message.text]), [
    ['user', 'stored question A'], ['assistant', 'stored answer A'],
  ])
  await chat.state.openSession('B')
  assert.deepEqual(Array.from(chat.state.messages.value, message => [message.role, message.text]), [
    ['user', 'stored question B'], ['assistant', 'stored answer B'],
  ])
  assert.equal(chat.state.streamRunning.value, false)

  const followup = await startMessage(chat, 'followup to stored B')
  assert.equal(followup.request.payload.session_id, 'B')
  followup.request.respond()
  followup.request.emit(['delta', 'new answer B'], ['done', {}])
  await followup.completion
  await settle()
  assert.deepEqual(Array.from(chat.state.messages.value, message => [message.role, message.text]), [
    ['user', 'stored question B'], ['assistant', 'stored answer B'],
    ['user', 'followup to stored B'], ['assistant', 'new answer B'],
  ])
  assert.equal(chat.state.streamRunning.value, false)
})

test('sending while B history loads preserves the input and waits to append to B history', async t => {
  const chat = await mountChat(t)
  chat.transport.setJson('/api/sessions/A/messages', {
    messages: [
      { id: 41, role: 'user', content: 'previous question A', created_at: '2026-10-30T02:00:00Z', extra: {} },
      { id: 42, role: 'assistant', content: 'previous answer A', created_at: '2026-10-30T02:00:01Z', extra: {} },
    ],
  })
  await chat.state.openSession('A')
  const releaseHistory = chat.transport.deferJson('/api/sessions/B/messages')
  const openingB = chat.state.openSession('B')
  await until(() => chat.transport.requests.some(request => request.path === '/api/sessions/B/messages'), 'B history request')

  chat.state.prompt.value = 'new question B'
  const firstAttempt = chat.state.addMessage()
  await settle()
  assert.equal(chat.transport.streams().length, 0, 'B must not submit while its history is still loading')
  assert.equal(chat.state.prompt.value, 'new question B', 'a blocked send must preserve the typed question')

  releaseHistory({
    messages: [
      { id: 51, role: 'user', content: 'previous question B', created_at: '2026-10-30T02:01:00Z', extra: {} },
      { id: 52, role: 'assistant', content: 'previous answer B', created_at: '2026-10-30T02:01:01Z', extra: {} },
    ],
  })
  await openingB
  await firstAttempt
  const submitted = chat.state.addMessage()
  await until(() => chat.transport.streams().length === 1, 'new B request after history loads')
  const request = chat.transport.streams()[0]
  assert.equal(request.payload.session_id, 'B')
  assert.equal(request.payload.message, 'new question B')
  request.respond()
  request.emit(['delta', 'new answer B'], ['done', {}])
  await submitted
  await settle()
  assert.deepEqual(Array.from(chat.state.messages.value, message => [message.role, message.text]), [
    ['user', 'previous question B'], ['assistant', 'previous answer B'],
    ['user', 'new question B'], ['assistant', 'new answer B'],
  ])
})

test('stop during an active-agent update cancels the prepared send before its stream POST', async t => {
  const chat = await mountChat(t)
  chat.transport.setJson('/api/sessions/A/messages', {
    messages: [
      { id: 61, role: 'user', content: 'existing question A', created_at: '2026-10-30T03:00:00Z', extra: {} },
      { id: 62, role: 'assistant', content: 'existing answer A', created_at: '2026-10-30T03:00:01Z', extra: {} },
    ],
  })
  chat.state.conversations.value = [{
    id: 'A', title: 'Existing A', meta: '2 条消息', agent_id: 1,
  }]
  await chat.state.openSession('A')
  chat.state.agentId.value = 2
  const releaseAgent = chat.transport.deferJson('/api/sessions/A/active-agent')
  const sending = chat.state.sendChatMessage('question that was stopped')
  await until(() => chat.transport.requests.some(request => request.path === '/api/sessions/A/active-agent'), 'active-agent update')
  const update = chat.transport.requests.find(request => request.path === '/api/sessions/A/active-agent')
  assert.equal(update.method, 'PUT')
  assert.equal(update.payload.agent_id, 2)
  assert.equal(chat.state.streamRunning.value, true)

  const releaseStop = chat.transport.deferJson('/api/chat/stop/A')
  const stopping = chat.state.stopStream()
  await until(() => chat.transport.requests.some(request => request.path === '/api/chat/stop/A'), 'stop during preparation')
  releaseStop({ session_id: 'A', stopped: false })
  await stopping
  releaseAgent({ session_id: 'A', agent_id: 2, active: true })
  await settle()
  assert.equal(chat.transport.streams().length, 0, 'a stopped preparation must not submit a new model request')
  await sending
  assert.equal(chat.state.streamRunning.value, false)
  assert.deepEqual(Array.from(chat.state.messages.value, message => [message.role, message.text]), [
    ['user', 'existing question A'], ['assistant', 'existing answer A'],
  ])
})
