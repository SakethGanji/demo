import { describe, expect, it } from 'vitest'
import { previewRequestUrl, previewUserHeaders } from './requestPolicy'

describe('preview request boundary', () => {
  const origin = 'https://engine.example'
  const routes = [{ method: 'GET', path: '/orders/{id}' }]
  const resolve = (url: string, method = 'GET') => previewRequestUrl(url, method, origin, 'demo', routes)
  it('allows selected routes with path parameters and queries', () => {
    expect(resolve('/api/p/demo/orders/123?detail=true').search).toBe('?detail=true')
  })
  it.each([
    '/api/projects', '/api/p/other/orders/123', '/api/p/demo/orders/123/extra',
    '/api/p/demo/unselected', 'https://other.example/api/p/demo/orders/123',
    '/api/p/demo/orders/%2fsecret', '/api/p/demo/orders/../../projects',
    'https://user:password@engine.example/api/p/demo/orders/123',
  ])('blocks unauthorized destination %s', (url) => expect(() => resolve(url)).toThrow('blocked'))
  it('blocks unapproved methods and absent route bindings', () => {
    expect(() => resolve('/api/p/demo/orders/123', 'DELETE')).toThrow('blocked')
    expect(() => previewRequestUrl('/api/p/demo/orders/123', 'GET', origin, 'demo', [])).toThrow('blocked')
  })
  it('permits only bound workflow runs, with POST and no cross-origin exception', () => {
    const path = '/api/apps/app_demo/workflow/wf_demo/run'
    const request = (url: string, method: string) => previewRequestUrl(url, method, origin, 'demo', [], [path])
    expect(request(path, 'POST').pathname).toBe(path)
    expect(() => request(path, 'GET')).toThrow('blocked')
    expect(() => request('/api/apps/app_other/workflow/wf_demo/run', 'POST')).toThrow('blocked')
    expect(() => request('https://other.example' + path, 'POST')).toThrow('blocked')
  })
})

describe('preview scope vs published scope', () => {
  const origin = 'https://engine.example'
  const routes = [{ method: 'GET', path: '/orders/{id}' }]
  it('published scope keeps the declared routes only', () => {
    expect(() => previewRequestUrl('/api/p/demo/refunds', 'GET', origin, 'demo', routes, [], 'published')).toThrow('blocked')
  })
  it('preview scope opens every route of the app\'s own project', () => {
    expect(previewRequestUrl('/api/p/demo/refunds?x=1', 'POST', origin, 'demo', routes, [], 'preview').pathname).toBe('/api/p/demo/refunds')
    expect(previewRequestUrl('/api/p/demo/refunds', 'GET', origin, 'demo', [], [], 'preview').pathname).toBe('/api/p/demo/refunds')
  })
  it.each([
    '/api/p/other/orders/1', '/api/projects', 'https://other.example/api/p/demo/refunds', '/api/p/demo/../../projects', '/api/p/demo',
  ])('preview scope still blocks %s', (url) => expect(() => previewRequestUrl(url, 'GET', origin, 'demo', routes, [], 'preview')).toThrow('blocked'))
})

describe('view-as headers', () => {
  it('sends nothing for the app itself', () => {
    expect(previewUserHeaders(null)).toEqual({})
    expect(previewUserHeaders({ id: ' ', name: 'x', roles: [] })).toEqual({})
  })
  it('uses the exact header names', () => {
    expect(previewUserHeaders({ id: 'alice', name: 'Alice', roles: ['staff', ' admin '] })).toEqual({
      'X-Preview-User': 'alice', 'X-Preview-User-Name': 'Alice', 'X-Preview-User-Roles': 'staff,admin',
    })
    expect(previewUserHeaders({ id: 'bob', name: '', roles: [] })).toEqual({ 'X-Preview-User': 'bob' })
  })
})
