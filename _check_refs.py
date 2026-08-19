# -*- coding: utf-8 -*-
import yaml, re, sys
sys.stdout.reconfigure(encoding='utf-8')
doc = yaml.safe_load(open(r'dify/workflows/cs_agent_chatbot.yml', encoding='utf-8'))
g = doc['workflow']['graph']
nodes = {n['id']: n for n in g['nodes']}
env_names = {v['name'] for v in doc['workflow']['environment_variables']}

def walk(o, path=''):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from walk(v, path + '.' + k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk(v, path + '[%d]' % i)
    else:
        yield path, o

problems = []
for nid, n in nodes.items():
    for path, v in walk(n['data']):
        if isinstance(v, str):
            for m in re.finditer(r'\{\{#([^}#]+)#\}\}', v):
                ref = m.group(1)
                parts = ref.split('.')
                if parts[0] == 'sys':
                    continue
                if parts[0] == 'env':
                    if parts[1] not in env_names:
                        problems.append((nid, path, ref, 'env 不存在'))
                    continue
                if parts[0] not in nodes:
                    problems.append((nid, path, ref, '节点不存在'))
                else:
                    out = nodes[parts[0]]['data'].get('outputs') or {}
                    if parts[1] not in out:
                        problems.append((nid, path, ref,
                            '输出 %s 不存在(节点类型 %s)' % (parts[1], nodes[parts[0]]['data']['type'])))
print('引用问题:')
for p in problems:
    print(' ', p)
print('共', len(problems), '个')
