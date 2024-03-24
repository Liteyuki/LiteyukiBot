import json
import os
import sqlite3
import types
from abc import ABC
from collections.abc import Iterable

import nonebot
from pydantic import BaseModel
from typing import Any

BaseIterable = list | tuple | set | dict


class LiteModel(BaseModel):
    """轻量级模型基类
    类型注解统一使用Python3.9的PEP585标准，如需使用泛型请使用typing模块的泛型类型
    """
    id: int = None


class BaseORMAdapter(ABC):
    def __init__(self):
        pass

    def auto_migrate(self, *args, **kwargs):
        """自动迁移

        Returns:

        """
        raise NotImplementedError

    def upsert(self, *args, **kwargs):
        """存储数据

        Returns:

        """
        raise NotImplementedError

    def first(self, *args, **kwargs):
        """查询第一条数据

        Returns:

        """
        raise NotImplementedError

    def all(self, *args, **kwargs):
        """查询所有数据

        Returns:

        """
        raise NotImplementedError

    def delete(self, *args, **kwargs):
        """删除数据

        Returns:

        """
        raise NotImplementedError

    def update(self, *args, **kwargs):
        """更新数据

        Returns:

        """
        raise NotImplementedError


class Database(BaseORMAdapter):
    """SQLiteORM适配器，严禁使用`FORIEGNID`和`JSON`作为主键前缀，严禁使用`$ID:`作为字符串值前缀

    Attributes:

    """
    type_map = {
            # default: TEXT
            str  : 'TEXT',
            int  : 'INTEGER',
            float: 'REAL',
            bool : 'INTEGER',
            list : 'TEXT'
    }

    DEFAULT_VALUE = {
            'TEXT'   : '',
            'INTEGER': 0,
            'REAL'   : 0.0
    }

    FOREIGNID = 'FOREIGNID'
    JSON = 'JSON'
    LIST = 'LIST'
    DICT = 'DICT'
    ID = '$ID'

    def __init__(self, db_name: str):
        super().__init__()
        if not os.path.exists(os.path.dirname(db_name)):
            os.makedirs(os.path.dirname(db_name))
        self.conn = sqlite3.connect(db_name)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()

    def auto_migrate(self, *args: type(LiteModel)):
        """自动迁移，检测新模型字段和原有表字段的差异，如有差异自动增删新字段

        Args:
            *args: 模型类

        Returns:

        """
        table_name = ''
        for model in args:
            model: type(LiteModel)
            # 检测并创建表，若模型未定义id字段则使用自增主键，有定义的话使用id字段，且id有可能为字符串
            table_name = model.__name__
            if 'id' in model.__annotations__ and model.__annotations__['id'] is not None:
                # 如果模型定义了id字段，那么使用模型的id字段
                id_type = self.type_map.get(model.__annotations__['id'], 'TEXT')
                self.cursor.execute(f'CREATE TABLE IF NOT EXISTS {table_name} (id {id_type} PRIMARY KEY)')
            else:
                # 如果模型未定义id字段，那么使用自增主键
                self.cursor.execute(f'CREATE TABLE IF NOT EXISTS {table_name} (id INTEGER PRIMARY KEY AUTOINCREMENT)')
            # 获取表字段
            self.cursor.execute(f'PRAGMA table_info({table_name})')
            table_fields = self.cursor.fetchall()
            table_fields = [field[1] for field in table_fields]

            raw_fields, raw_types = zip(*model.__annotations__.items())
            # 获取模型字段，若有模型则添加FOREIGNID前缀，若为BaseIterable则添加JSON前缀，用多行if判断
            model_fields = []
            model_types = []
            for field, r_type in zip(raw_fields, raw_types):
                if isinstance(r_type, type(LiteModel)):
                    model_fields.append(f'{self.FOREIGNID}{field}')
                    model_types.append('TEXT')
                elif r_type in [list[str], list[int], list[float], list[bool], list]:
                    model_fields.append(f'{self.LIST}{field}')
                    model_types.append('TEXT')
                elif r_type in [dict[str, str], dict[str, int], dict[str, float], dict[str, bool], dict]:
                    model_fields.append(f'{self.DICT}{field}')
                    model_types.append('TEXT')
                elif isinstance(r_type, types.GenericAlias):
                    model_fields.append(f'{self.JSON}{field}')
                    model_types.append('TEXT')
                else:
                    model_fields.append(field)
                    model_types.append(self.type_map.get(r_type, 'TEXT'))

            # 检测新字段或字段类型是否有变化，有则增删字段，已经加了前缀类型
            for field_changed, type_, r_type in zip(model_fields, model_types, raw_types):
                if field_changed not in table_fields:
                    nonebot.logger.debug(f'ALTER TABLE {table_name} ADD COLUMN {field_changed} {type_}')
                    self.cursor.execute(f'ALTER TABLE {table_name} ADD COLUMN {field_changed} {type_}')
                    # 在原有的行中添加新字段对应类型的默认值，从DEFAULT_TYPE中获取
                    self.cursor.execute(f'UPDATE {table_name} SET {field_changed} = ? WHERE {field_changed} IS NULL', (self.DEFAULT_VALUE.get(type_, ""),))

            # 检测多余字段，除了id字段
            for field in table_fields:
                if field not in model_fields and field != 'id':
                    nonebot.logger.debug(f'ALTER TABLE {table_name} DROP COLUMN {field}')
                    self.cursor.execute(f'ALTER TABLE {table_name} DROP COLUMN {field}')

        self.conn.commit()
        nonebot.logger.debug(f'Table {table_name} migrated successfully')

    def upsert(self, *models: LiteModel) -> int | tuple:
        """存储数据，检查id字段，如果有id字段则更新，没有则插入

        Args:
            models: 数据

        Returns:
            id: 数据id，如果有多个数据则返回id元组
        """

        ids = []
        for model in models:
            table_name = model.__class__.__name__
            if not self._detect_for_table(table_name):
                raise ValueError(f'表{table_name}不存在，请先迁移')
            key_list = []
            value_list = []
            # 处理外键，添加前缀'$IDFieldName'
            for field, value in model.__dict__.items():
                if isinstance(value, LiteModel):
                    key_list.append(f'{self.FOREIGNID}{field}')
                    value_list.append(f'{self.ID}:{value.__class__.__name__}:{self.upsert(value)}')
                elif isinstance(value, list):
                    key_list.append(f'{self.LIST}{field}')
                    value_list.append(self._flat(value))
                elif isinstance(value, dict):
                    key_list.append(f'{self.DICT}{field}')
                    value_list.append(self._flat(value))
                elif isinstance(value, BaseIterable):
                    key_list.append(f'{self.JSON}{field}')
                    value_list.append(self._flat(value))
                else:
                    key_list.append(field)
                    value_list.append(value)
            # 更新或插入数据，用?占位
            nonebot.logger.debug(f'INSERT OR REPLACE INTO {table_name} ({",".join(key_list)}) VALUES ({",".join(["?" for _ in key_list])})')
            self.cursor.execute(f'INSERT OR REPLACE INTO {table_name} ({",".join(key_list)}) VALUES ({",".join(["?" for _ in key_list])})', value_list)

            ids.append(self.cursor.lastrowid)
        self.conn.commit()
        return ids[0] if len(ids) == 1 else tuple(ids)

    def _flat(self, data: Iterable) -> str:
        """扁平化数据，返回扁平化对象

        Args:

            data: 数据，可迭代对象

        Returns: json字符串
        """
        if isinstance(data, dict):
            return_data = {}
            for k, v in data.items():
                if isinstance(v, LiteModel):
                    return_data[f'{self.FOREIGNID}{k}'] = f'{self.ID}:{v.__class__.__name__}:{self.upsert(v)}'
                elif isinstance(v, list):
                    return_data[f'{self.LIST}{k}'] = self._flat(v)
                elif isinstance(v, dict):
                    return_data[f'{self.DICT}{k}'] = self._flat(v)
                elif isinstance(v, BaseIterable):
                    return_data[f'{self.JSON}{k}'] = self._flat(v)
                else:
                    return_data[k] = v

        elif isinstance(data, list | tuple | set):
            return_data = []
            for v in data:
                if isinstance(v, LiteModel):
                    return_data.append(f'{self.ID}:{v.__class__.__name__}:{self.upsert(v)}')
                elif isinstance(v, list):
                    return_data.append(self._flat(v))
                elif isinstance(v, dict):
                    return_data.append(self._flat(v))
                elif isinstance(v, BaseIterable):
                    return_data.append(self._flat(v))
                else:
                    return_data.append(v)
        else:
            raise ValueError('数据类型错误')

        return json.dumps(return_data)

    def _detect_for_table(self, table_name: str) -> bool:
        """在进行增删查改前检测表是否存在

        Args:
            table_name: 表名

        Returns:

        """
        return self.cursor.execute(f'SELECT * FROM sqlite_master WHERE type = "table" AND name = ?', (table_name,)).fetchone()

    def first(self, model: type(LiteModel), conditions, *args, default: Any = None) -> LiteModel | None:
        """查询第一条数据

        Args:
            model: 模型
            conditions: 查询条件
            *args: 参数化查询条件参数
            default: 未查询到结果默认返回值

        Returns: 数据
        """
        table_name = model.__name__

        if not self._detect_for_table(table_name):
            return default

        self.cursor.execute(f'SELECT * FROM {table_name} WHERE {conditions}', args)
        if row_data := self.cursor.fetchone():
            data = dict(row_data)
            return model(**self.convert_to_dict(data))
        return default

    def all(self, model: type(LiteModel), conditions=None, *args, default: Any = None) -> list[LiteModel] | None:
        """查询所有数据

        Args:
            model: 模型
            conditions: 查询条件
            *args: 参数化查询条件参数
            default: 未查询到结果默认返回值

        Returns: 数据
        """
        table_name = model.__name__

        if not self._detect_for_table(table_name):
            return default

        if conditions:
            self.cursor.execute(f'SELECT * FROM {table_name} WHERE {conditions}', args)
        else:
            self.cursor.execute(f'SELECT * FROM {table_name}')
        if row_datas := self.cursor.fetchall():
            datas = [dict(row_data) for row_data in row_datas]
            return [model(**self.convert_to_dict(d)) for d in datas] if datas else default
        return default

    def delete(self, model: type(LiteModel), conditions, *args):
        """删除数据

        Args:
            model: 模型
            conditions: 查询条件
            *args: 参数化查询条件参数

        Returns:

        """
        table_name = model.__name__

        if not self._detect_for_table(table_name):
            return
        nonebot.logger.debug(f'DELETE FROM {table_name} WHERE {conditions}')
        self.cursor.execute(f'DELETE FROM {table_name} WHERE {conditions}', args)
        self.conn.commit()

    def convert_to_dict(self, data: dict) -> dict:
        """将json字符串转换为字典

        Args:
            data: json字符串

        Returns: 字典
        """

        def load(d: BaseIterable) -> BaseIterable:
            """递归加载数据，去除前缀"""
            if isinstance(d, dict):
                new_d = {}
                for k, v in d.items():
                    if k.startswith(self.FOREIGNID):
                        new_d[k.replace(self.FOREIGNID, '')] = load(
                            dict(self.cursor.execute(f'SELECT * FROM {v.split(":", 2)[1]} WHERE id = ?', (v.split(":", 2)[2],)).fetchone()))

                    elif k.startswith(self.LIST):
                        if v == '': v = '[]'
                        new_d[k.replace(self.LIST, '')] = load(json.loads(v))
                    elif k.startswith(self.DICT):
                        if v == '': v = '{}'
                        new_d[k.replace(self.DICT, '')] = load(json.loads(v))
                    elif k.startswith(self.JSON):
                        if v == '': v = '[]'
                        new_d[k.replace(self.JSON, '')] = load(json.loads(v))
                    else:
                        new_d[k] = v
            elif isinstance(d, list | tuple | set):
                new_d = []
                for i, v in enumerate(d):
                    if isinstance(v, str) and v.startswith(self.ID):
                        new_d.append(load(dict(self.cursor.execute(f'SELECT * FROM {v.split(":", 2)[1]} WHERE id = ?', (v.split(":", 2)[2],)).fetchone())))
                    elif isinstance(v, BaseIterable):
                        new_d.append(load(v))
            else:
                new_d = d
            return new_d

        return load(data)
