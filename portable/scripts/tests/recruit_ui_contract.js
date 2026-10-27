/* Offline interaction contract for the shipped page. No browser, model or server.
 * This checks payloads and state changes; it does not claim visual/browser QA.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../../recruit/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
const settle = () => new Promise(resolve => setImmediate(resolve));
const submitEvent = () => ({preventDefault() {}});

class Element {
  constructor(tag = 'div', id = '') {
    this.tagName = tag;
    this.id = id;
    this.value = '';
    this.textContent = '';
    this.children = [];
    this.listeners = {};
    this.dataset = {};
    this.attrs = {};
    this.classList = {remove() {}};
    this.files = [];
    this.disabled = false;
    this.hidden = false;
  }
  set innerHTML(value) { throw new Error('Untrusted content must not use innerHTML'); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(name, value) { this.attrs[name] = value; }
  scrollIntoView() {}
}

async function harness(overrides = {}) {
  const elements = {};
  for (const match of html.matchAll(/<(\w+)\b[^>]*\bid="([^"]+)"/g)) elements[match[2]] = new Element(match[1], match[2]);
  const buttons = ['jd', 'screen', 'interview', 'qa'].map(task => {
    const element = new Element('button'); element.dataset.task = task; return element;
  });
  const generated = [];
  const document = {
    getElementById: id => elements[id],
    createElement: tag => { const element = new Element(tag); generated.push(element); return element; },
    querySelectorAll: selector => selector === '[data-task]' ? buttons : [...Object.values(elements), ...buttons, ...generated].filter(element => ['button', 'input', 'textarea'].includes(element.tagName)),
  };
  const config = {
    model: 'deepseek-flash', company_info: '青禾智选（虚构）', has_api_key: true,
    allow_tools: false, ocr_enabled: true, default_jd: '新媒体运营 JD',
    limits: {screen: 300, qa: 800, jd: 100, interview: 100},
    usage: {month: '2026-10', screen: 0, qa: 0, jd: 0, interview: 0}, ...overrides,
  };
  const calls = [];
  const behavior = {failRun: false, beforeExtract: null};
  async function fetch(url, options) {
    assert.equal(options.headers['X-Recruit-Token'], '__RECRUIT_TOKEN__');
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({url, body});
    if (url === '/api/config' && body) {
      config.has_api_key = Boolean(body.api_key) || config.has_api_key;
      for (const key of ['model', 'company_info', 'limits']) config[key] = body[key];
    }
    if (url === '/api/extract') {
      if (behavior.beforeExtract) await behavior.beforeExtract(body);
      if (body.name.startsWith('fail')) return {ok: false, json: async () => ({error: '识别文字太少，请重新拍照。本次不计用量。'})};
      const ocr = /\.(pdf|jpg|jpeg|png|webp|bmp)$/i.test(body.name);
      return {ok: true, json: async () => ({name: body.name, text: '<script>虚构简历，不执行</script>\n运营经验 2 年', extraction: {method: ocr ? 'ocr' : 'text', ocr_pages: ocr ? 1 : 0, text_pages: /\.pdf$/i.test(body.name) ? 1 : 0}})};
    }
    if (url === '/api/run') {
      config.usage[body.task] += body.task === 'screen' ? body.resumes.length : 1;
      if (behavior.failRun) return {ok: false, json: async () => ({error: '本月额度已用完'})};
      return {ok: true, json: async () => ({answer: '<img src=x onerror=alert(1)>\n虚构结果', usage: {...config.usage}})};
    }
    return {ok: true, json: async () => JSON.parse(JSON.stringify(config))};
  }
  class FileReader {
    readAsDataURL(file) { this.result = 'data:application/octet-stream;base64,' + Buffer.from(file.name).toString('base64'); this.onload(); }
  }
  vm.runInContext(script, vm.createContext({document, fetch, FileReader, navigator: {clipboard: {writeText: async () => {}}}}));
  await settle();
  const upload = async names => {
    elements.files.files = names.map(name => typeof name === 'string' ? {name, size: 10} : name);
    await elements.files.listeners.change({target: elements.files});
  };
  const select = task => buttons.find(button => button.dataset.task === task).listeners.click();
  const run = () => elements['task-form'].listeners.submit(submitEvent());
  const list = () => elements.resumes.children;
  const editor = index => list()[index].children[2].children[1];
  const checkbox = index => list()[index].children[0].children[0].children[0];
  const remove = index => list()[index].children[0].children[1].listeners.click();
  return {elements, config, calls, behavior, upload, select, run, list, editor, checkbox, remove};
}

const tests = [
  ['config, secret clearing, JD handoff and literal output', async () => {
    const h = await harness({has_api_key: false});
    assert.equal(h.elements['current-jd'].value, '新媒体运营 JD');
    assert.equal(h.elements['config-details'].open, true);
    h.elements['api-key'].value = 'test-not-a-real-key';
    await h.elements['config-form'].listeners.submit(submitEvent());
    assert.equal(h.elements['api-key'].value, '');
    await h.run();
    assert(h.elements.output.textContent.startsWith('<img'));
    h.elements['use-jd'].listeners.click();
    assert.equal(h.elements['current-jd'].value, h.elements.output.textContent);
    assert.equal(h.elements['resume-field'].hidden, false);
  }],
  ['local OCR progress, serial extraction and editable preview', async () => {
    const h = await harness(); h.select('screen');
    let active = 0;
    h.behavior.beforeExtract = async body => {
      active += 1; assert.equal(active, 1); assert.equal(h.elements.run.disabled, true);
      assert(h.elements.message.textContent.includes(body.name));
      assert(h.elements.message.textContent.includes('本地 OCR'));
      await settle(); active -= 1;
    };
    await h.upload(['照片.JPG', '混合.pdf']);
    assert.equal(h.list().length, 2);
    assert.equal(h.list()[0].children[2].open, true);
    assert(h.list()[1].children[1].textContent.includes('文字层 1 页'));
    assert(h.editor(0).value.startsWith('<script>'));
    assert.equal(h.editor(0).maxLength, 20000);
    h.editor(0).value = '校正后的工作经历：运营 3 年'; h.editor(0).listeners.input();
    await h.run();
    const payload = h.calls.filter(call => call.url === '/api/run').at(-1).body;
    assert.equal(payload.resumes[0].text, '校正后的工作经历：运营 3 年');
    assert.deepEqual(Object.keys(payload.resumes[0]).sort(), ['name', 'text']);
    assert.equal(h.config.usage.screen, 2);
  }],
  ['all supported image extensions and five-file limit', async () => {
    const h = await harness();
    await h.upload(['one.jpeg', 'two.png', 'three.webp', 'four.bmp', 'five.jpg']);
    assert.equal(h.list().length, 5);
    const before = h.calls.length; await h.upload(['six.jpg']);
    assert.equal(h.calls.length, before); assert(h.elements.message.textContent.includes('最多保留 5'));
    h.remove(0); assert.equal(h.list().length, 4);
  }],
  ['DOC conversion and file size/type rejections', async () => {
    const h = await harness(); const before = h.calls.length;
    await h.upload(['legacy.DOC']); assert(h.elements.message.textContent.includes('另存为 DOCX 或 PDF'));
    await h.upload([{name: 'large.jpg', size: 5 * 1024 * 1024 + 1}]); assert(h.elements.message.textContent.includes('5 MiB'));
    await h.upload(['unsafe.exe']); assert(h.elements.message.textContent.includes('请上传'));
    assert.equal(h.calls.length, before);
  }],
  ['OCR disabled notice rejects images but allows text PDF', async () => {
    const h = await harness({ocr_enabled: false});
    assert(h.elements['ocr-status'].textContent.includes('已关闭'));
    const before = h.calls.length; await h.upload(['photo.jpg']);
    assert.equal(h.calls.length, before); assert(h.elements.message.textContent.includes('已关闭'));
    h.behavior.beforeExtract = async () => assert(!h.elements.message.textContent.includes('本地 OCR'));
    await h.upload(['text.pdf']); assert.equal(h.list().length, 1);
  }],
  ['OCR failure preserves successful files and never submits scoring', async () => {
    const h = await harness(); const before = JSON.stringify(h.config.usage);
    await h.upload(['first.txt', 'fail-photo.png', 'not-reached.txt']);
    assert.equal(h.list().length, 1); assert.equal(h.list()[0].children[2].open, false);
    assert(h.elements.message.textContent.includes('不计用量'));
    assert.equal(JSON.stringify(h.config.usage), before);
    assert.equal(h.calls.filter(call => call.url === '/api/extract').length, 2);
    assert.equal(h.calls.filter(call => call.url === '/api/run').length, 0);
    assert.equal(h.elements.run.disabled, false);
  }],
  ['only selected corrected resumes are sent; QA excludes resumes', async () => {
    const h = await harness(); h.select('screen'); await h.upload(['a.txt', 'b.txt']);
    h.checkbox(1).checked = false; h.checkbox(1).listeners.change();
    await h.run(); assert.equal(h.calls.at(-1).body.resumes.length, 1);
    h.select('qa'); await h.run(); assert.equal(h.calls.at(-1).body.resumes.length, 0);
  }],
  ['invalid edited text blocks scoring; model failure restores controls', async () => {
    const h = await harness(); h.select('screen'); await h.upload(['a.txt']);
    for (const value of ['', 'x'.repeat(20001)]) {
      h.editor(0).value = value; h.editor(0).listeners.input();
      const before = h.calls.length; await h.run(); assert.equal(h.calls.length, before);
      assert(h.elements.message.textContent.includes('20000'));
    }
    h.editor(0).value = '已校正的简历'; h.editor(0).listeners.input();
    h.behavior.failRun = true; await h.run();
    assert(h.elements.message.textContent.includes('额度已用完')); assert.equal(h.elements.run.disabled, false);
  }],
];

(async () => {
  for (const [name, test] of tests) { await test(); console.log('PASS ' + name); }
  console.log('Recruit UI: ' + tests.length + ' interaction contracts passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
