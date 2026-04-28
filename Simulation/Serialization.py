import sys
from marshmallow import Schema, pre_load, post_load, post_dump


class SingleFieldSchema(Schema):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.__field__ = next(iter(self.fields.values()))

	@pre_load
	def add_layer(self, data, **kwargs):
		return { self.__field__.name: data }

	@post_load
	def make_object(self, data, **kwargs):
		module = sys.modules[self.__module__]
		return getattr(module, self.clazz)(**data)

	@post_dump
	def remove_layer(self, data, **kwargs):
		return data[self.__field__.name]


class ObjectSchema(Schema):
	@post_load
	def make_object(self, data, **kwargs):
		module = sys.modules[self.__module__]
		return getattr(module, self.clazz)(**data)
