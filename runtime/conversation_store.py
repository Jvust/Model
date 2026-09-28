"""Per-running-model local conversations. No caller-chosen filesystem paths."""
from __future__ import annotations
try:
    from .activation_store import digest
except ImportError:
    from activation_store import digest


def identity(state):
    if not state.get('ready') or not state.get('source_drive_file_id'):
        return None
    return digest({'domain':'chat-history-v1', 'source_file':state['source_drive_file_id'],
                   'version':state.get('source_version'), 'cached_identity':state.get('cache_file'), 'model':state.get('model'),
                   'package':state.get('model_relative_path')})


def normalize(messages):
    if not isinstance(messages, list) or len(messages)>200:
        raise ValueError('Chat history must contain at most 200 messages')
    result=[]
    for message in messages:
        if not isinstance(message, dict) or set(message) != {'role','content'} or message['role'] not in {'user','assistant'} or not isinstance(message['content'],str):
            raise ValueError('Invalid chat history message')
        result.append(dict(message))
    if sum(len(item['content'].encode('utf-8')) for item in result)>1_000_000:
        raise ValueError('Chat history exceeds 1 MB; export or start a new conversation')
    return result


def read(store, state):
    key=identity(state)
    return {'identity':key, 'messages':store.load_workspace(key).get('messages',[]) if key else []}


def write(store, state, expected_identity, messages):
    key=identity(state)
    if not key or key!=expected_identity:
        raise ValueError('当前运行模型已变化，拒绝把对话写进另一个模型')
    store.save_workspace(key, {'messages':normalize(messages)})
    return {'identity':key, 'saved':True}
