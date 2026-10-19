import { describe, expect, it } from 'vitest'
import { CSP_META, emailMeta, findHtmlReport, injectCsp, looksLikeHtml, outputView, reportFilename } from './htmlReport'

describe('looksLikeHtml', () => {
  it('accepts documents', () => {
    expect(looksLikeHtml('<!DOCTYPE html><html><body>x</body></html>')).toBe(true)
    expect(looksLikeHtml('  \n<!doctype html>\n<p>x</p>')).toBe(true)
    expect(looksLikeHtml('<html lang="en"><body></body></html>')).toBe(true)
    expect(looksLikeHtml('﻿<html>')).toBe(true)
  })
  it('accepts fragments with block tags', () => {
    expect(looksLikeHtml('<h1>Report</h1>')).toBe(true)
    expect(looksLikeHtml('Intro text <table><tr><td>1</td></tr></table>')).toBe(true)
    expect(looksLikeHtml('<div class="card">x</div>')).toBe(true)
  })
  it('rejects plain text, inline-only markup and non-strings', () => {
    expect(looksLikeHtml('hello world')).toBe(false)
    expect(looksLikeHtml('a < b and c > d')).toBe(false)
    expect(looksLikeHtml('<b>bold</b> and <a href="x">link</a>')).toBe(false)
    expect(looksLikeHtml('line<br>break')).toBe(false)
    expect(looksLikeHtml('<header-ish>')).toBe(false)
    expect(looksLikeHtml(42)).toBe(false)
    expect(looksLikeHtml(null)).toBe(false)
    expect(looksLikeHtml(undefined)).toBe(false)
  })
})

describe('injectCsp', () => {
  it('puts the meta right after <head> in a full document', () => {
    const doc = '<!doctype html><html lang="en"><head><title>T</title></head><body><p>x</p></body></html>'
    expect(injectCsp(doc)).toBe(
      `<!doctype html><html lang="en"><head>${CSP_META}<title>T</title></head><body><p>x</p></body></html>`,
    )
  })
  it('keeps <head> attributes and leading whitespace', () => {
    const doc = '\n  <!DOCTYPE html>\n<html>\n<head data-x="1">\n<style>p{}</style></head></html>'
    const out = injectCsp(doc)
    expect(out).toContain(`<head data-x="1">${CSP_META}\n<style>`)
    expect(out.indexOf(CSP_META)).toBeLessThan(out.indexOf('<style>'))
  })
  it('puts the meta after <html> when there is no <head>', () => {
    expect(injectCsp('<!doctype html><html><body><h1>x</h1></body></html>')).toBe(
      `<!doctype html><html>${CSP_META}<body><h1>x</h1></body></html>`,
    )
    expect(injectCsp('<html><body>x</body></html>')).toBe(`<html>${CSP_META}<body>x</body></html>`)
  })
  it('puts the meta after a bare doctype', () => {
    expect(injectCsp('<!doctype html><title>t</title><p>x</p>')).toBe(`<!doctype html>${CSP_META}<title>t</title><p>x</p>`)
  })
  it('does not let content before a later <head> run ahead of the policy', () => {
    const doc = '<!doctype html><script>steal()</script><html><head></head><body></body></html>'
    const out = injectCsp(doc)
    expect(out.indexOf(CSP_META)).toBeLessThan(out.indexOf('<script>'))
    expect(out.match(/Content-Security-Policy/g)).toHaveLength(1)
  })
  it('ignores a fake <head> inside a comment', () => {
    const out = injectCsp('<!doctype html><!-- <head> --><head></head>')
    expect(out.startsWith(`<!doctype html>${CSP_META}`)).toBe(true)
  })
  it('does not mistake <header> for <head>', () => {
    const out = injectCsp('<header>x</header>')
    expect(out).toBe(`<!doctype html><html><head><meta charset="utf-8">${CSP_META}</head><body><header>x</header></body></html>`)
  })
  it('wraps a fragment in a full document', () => {
    expect(injectCsp('<h1>Hi</h1><p>there</p>')).toBe(
      `<!doctype html><html><head><meta charset="utf-8">${CSP_META}</head><body><h1>Hi</h1><p>there</p></body></html>`,
    )
  })
  it('uses the strict policy', () => {
    expect(CSP_META).toBe(
      `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: https:; style-src 'unsafe-inline'">`,
    )
  })
})

describe('emailMeta', () => {
  it('reads a SendEmail result', () => {
    expect(emailMeta({ sent: true, to: 'a@x.io', subject: 'Hi', html: '<p>x</p>', message_id: 'm1' })).toEqual({
      to: 'a@x.io',
      subject: 'Hi',
      sent: true,
      note: null,
    })
  })
  it('keeps the note when not sent and joins list recipients', () => {
    expect(emailMeta({ sent: false, to: ['a@x.io', 'b@x.io'], subject: 'S', note: 'SMTP is not configured' })).toEqual({
      to: 'a@x.io, b@x.io',
      subject: 'S',
      sent: false,
      note: 'SMTP is not configured',
    })
  })
  it('is null for non-email items', () => {
    expect(emailMeta({ html: '<p>x</p>', title: 'T' })).toBeNull()
    expect(emailMeta({ sent: 'yes', to: 'a' })).toBeNull()
    expect(emailMeta({ sent: true })).toBeNull()
  })
})

describe('findHtmlReport', () => {
  it('finds the first item with an HTML field', () => {
    const r = findHtmlReport([{ html: 'plain' }, { html: '<!doctype html><p>x</p>', title: ' Weekly ', text: 'x' }])
    expect(r).toEqual({ index: 1, html: '<!doctype html><p>x</p>', title: 'Weekly', email: null })
  })
  it('carries email meta for SendEmail items', () => {
    const r = findHtmlReport([{ sent: false, to: 't@x.io', subject: 'Report', html: '<div>r</div>', note: 'n' }])
    expect(r?.email).toEqual({ to: 't@x.io', subject: 'Report', sent: false, note: 'n' })
    expect(r?.title).toBeNull()
  })
  it('is null without an HTML item', () => {
    expect(findHtmlReport([{ a: 1 }, null, 'x', [1], { html: 3 }])).toBeNull()
    expect(findHtmlReport([])).toBeNull()
    expect(findHtmlReport(undefined)).toBeNull()
  })
})

describe('reportFilename', () => {
  it('slugs the title', () => {
    expect(reportFilename('Weekly sales: Q3!')).toBe('weekly-sales-q3.html')
    expect(reportFilename(null)).toBe('report.html')
    expect(reportFilename('***')).toBe('report.html')
  })
})

describe('outputView', () => {
  it('opens an HTML report on Preview unless the user picked a data view', () => {
    expect(outputView('schema', null, true)).toBe('preview')
    expect(outputView('json', true, true)).toBe('preview')
    expect(outputView('json', false, true)).toBe('json')
  })
  it('falls back to the data mode without a report', () => {
    expect(outputView('schema', true, false)).toBe('schema')
    expect(outputView('json', null, false)).toBe('json')
  })
})
