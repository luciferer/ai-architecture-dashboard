'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appPath = path.join(__dirname, '..', 'public', 'app.js');
const source = fs.readFileSync(appPath, 'utf8');
assert.match(source, /layoutKinds=mode==='components'\?componentLayoutKinds\(elements\):\[\];/, 'renderGraph must feed the current component projection into its layout');

function extractFunction(name) {
  const marker = `function ${name}(`;
  const start = source.indexOf(marker);
  assert.notEqual(start, -1, `missing ${name}`);
  const open = source.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    else if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`unterminated ${name}`);
}

const sandbox = {
  mode: 'runtime',
  client: 'all',
  positions: new Map(),
  layoutKinds: [],
  componentKinds: ['mcp', 'skill', 'plugin', 'runtime', 'cli', 'sdk', 'subagent', 'rule', 'suite', 'source', 'workflow', 'hook'],
  overviewKinds: ['mcp', 'skill', 'plugin', 'runtime'],
  snapshot: {clients: {codex: 'Codex'}},
  isArchitecture: () => false,
  cy: {nodes: () => []},
};
sandbox.positionKey = id => `${sandbox.mode}:${sandbox.client}:${id}`;
vm.runInNewContext(`${extractFunction('componentLayoutKinds')}\n${extractFunction('positionFor')}`, sandbox);

const runtimeNode = {id: 'observed-agent:sample', kind: 'trace_agent', positions: {runtime: {x: 620, y: 315}}};
const runtimePosition = sandbox.positionFor(runtimeNode);
assert.equal(runtimePosition.x, 620, 'runtime must use its explicit depth column');
assert.equal(runtimePosition.y, 315, 'runtime must use its explicit branch row');

sandbox.mode = 'components';
const demoMcpPosition = sandbox.positionFor({id: 'demo:mcp', kind: 'mcp', positions: {components: {x: 480, y: 110}}});
assert.equal(demoMcpPosition.x, 480, 'component demo should honor its explicit right-side column');
assert.equal(demoMcpPosition.y, 110, 'component demo should honor its explicit fan-out row');

const projected = [
  {data: {id: 'client:codex', kind: 'client'}},
  {data: {id: 'mcp:one', kind: 'mcp'}},
  {data: {id: 'hook:one', kind: 'hook'}},
  {data: {id: 'edge:one', source: 'client:codex', target: 'mcp:one'}},
];
sandbox.mode = 'components';
sandbox.layoutKinds = Array.from(sandbox.componentLayoutKinds(projected));
assert.deepEqual(sandbox.layoutKinds, ['mcp', 'hook'], 'only present component kinds should occupy columns');

const priorHook = {data: key => key === 'kind' ? 'hook' : undefined, position: () => ({x: 565, y: 110})};
sandbox.cy = {nodes: () => [priorHook]};
const hookPosition = sandbox.positionFor({id: 'hook:two', kind: 'hook'});
assert.equal(hookPosition.x, 565, 'hook column should be compressed beside the visible MCP column');
assert.equal(hookPosition.y, 214, 'same-kind components should remain in separate rows');

const missingCoordinatesPosition = sandbox.positionFor({id: 'mcp:live', kind: 'mcp'});
assert.equal(missingCoordinatesPosition.x, 340, 'live components without supplied coordinates should keep compressed layout');
assert.equal(missingCoordinatesPosition.y, 110, 'live components without supplied coordinates should keep default row placement');

console.log('runtime positions and visible-kind component layout: OK');
