import traceback
import copy
import sys

__all__ = ["Config", "Field", "RangeField", "ChoiceField", "ListField", "ConfigField", "RegistryField"]

def joinNamePath(prefix=None, name=None, index=None):
    """
    Utility function for generating nested configuration names
    """
    if not prefix and not name:
        raise ValueError("invalid name. cannot be None")
    elif not name:
        name = prefix
    elif prefix and name:
        name = prefix + "." + name

    if index is not None:
        return "%s[%s]"%(name, repr(index))
    else:
        return name

def typeString(aType):
    """
    Utility function for generating type strings.
    Used internally for saving Config to file
    """
    if aType is None:
        return None
    else:
        return aType.__module__+"."+aType.__name__


class _Bool(int):
    def __init__(self, value=None):
        bool.__init__(self, value)

    def __repr__(self):
        return bool.__repr__(bool(self))
    def __str__(self):
        return bool.__str__(bool(self))
    
    def __setattr__(self, attr, value):
        if attr != "history" and attr != "__doc__":
            return setattr(None, attr, value)
        else:
            self.__dict__[attr] = value

class _None(object):
    def __repr__(self):
        return repr(None)

    def __str__(self):
        return str(None)

    def __nonzero__(self):
        return False

    def __len__(self):
        return 0

    def __call__(self):
        return None

    def __eq__(self, other):
        return not other

    def __ne__(self, other):
        return not self.__eq__(other)

    def __setattr__(self, attr, value):
        if attr != "history" and attr != "__doc__":
            return setattr(None, attr, value)
        else:
            self.__dict__[attr] = value

class _List(list):
    def __init__(self, x=[]):
        list.__init__(self, x)
        self.history = []
        self.history.append((list(self), traceback.extract_stack()[:-1]))

    def __setitem__(self, i, x):
        list.__setitem__(self, i, x)
        self.history.append((list(self), traceback.extract_stack()[:-1]))
    
    def __delitem__(self, i):
        list.__delitem__(self, i)
        self.history.append((list(self), traceback.extract_stack()[:-1]))

    def __setslice__(self, i, x):
        list.__setslice__(self, i, x)
        self.history.append((list(self), traceback.extract_stack()[:-1]))

    def append(self, x):
        self[len(self):len(self)] = [x]

    def extend(self, x):
        self[len(self):len(self)] = x

    def insert(self, i, x):
        self[i:i] = [x]

    def pop(self, i=-1):
        list.pop(self, i)
        self.history.append((list(self), traceback.extract_stack()[:-1]))

    def remove(self, x):
        list.remove(self, i)
        self.history.append((list(self), traceback.extract_stack()[:-1]))
   
    def sort(self, cmp=None, key=None, reverse=False):
        list.sort(self, cmp, key, reverse)
        self.history.append((list(self), traceback.extract_stack()[:-1]))

    def __iadd__(self, y):
        list.__iadd__(self, y)
        self.history.append((list(self), traceback.extract_stack()[:-1]))
    
    def __imul__(self, y):
        list.__imul__(self, y)
        self.history.append((list(self), traceback.extract_stack()[:-1]))

class Registry(dict):
    def __init__(self, fullname, basetype, types, restricted):
        dict.__init__(self)
        self.fullname = fullname
        self.basetype = basetype
        self.restricted = restricted
        self.name = None
        self._active = None 
        self.types = copy.deepcopy(types) if types is not None else {}
        self.history = []
    
    active = property(lambda x: x[x.name] if x.name else None)

    def __getitem__(self, k):
        if k not in self.types:
            raise KeyError("Unknown key %s in Registry '%s'"%(repr(k), self.fullname))
        
        value = dict.get(self, k, None)
        if not value:
            try:
                history = value.history
            except AttributeError:
                history = None
            value = self.types[k]()
            value._rename(joinNamePath(name=self.fullname, index=k))
            if history:
                value.history = history
            dict.__setitem__(self, k, value)
        
        return value

    def __setitem__(self, k, value):
        #determine type
        dtype = self.types.get(k)
        if dtype is None:
            if self.restricted:            
                raise ValueError("Cannot register '%s' in restricted Registry %s"%\
                        (str(k), self.fullname))
            if isinstance(value, type) and issubclass(value, self.basetype):
                dtype = value
            elif isinstance(value, self.basetype):
                dtype = type(value)
            elif value is None:
                dtype = self.basetype
            else:
                raise ValueError("Invalid type %s. All values in Registry '%s' must be of type %s"%\
                        (dtype, self.fullname, self.basetype))
            self.types[k] = dtype
        
        #set value
        try:
            oldValue = dict.__getitem__(self, k)
            history = oldValue.history
        except KeyError, AttributeError:
            history = {}
            oldValue = None
        
        name=joinNamePath(name=self.fullname, index=k)
        if type(value) == dtype:
            storage = value._storage
        elif isinstance(value, dict):
            storage = value
        elif value == dtype:
            value = dtype()
            storage = value._storage
        elif value is not None:
            raise ValueError("Invalid type %s. Registry entry '%s' must be of type %s"%\
                    (type(value), name, dtype))
       
        if not value: 
            value = _None()
            value.history = history
            dict.__setitem__(self, k, value)
        else:
            if not oldValue:
                oldValue = dtype()
                oldValue._rename(name)
                dict.__setitem__(self, k, oldValue)
                oldValue.history = history
            oldValue.override(storage)

    def __delitem__(self, k):
        self[k]=None    

class ConfigMeta(type):
    """A metaclass for Config

    Adds a dictionary containing all Field class attributes
    as a class attribute called '_fields', and adds the name of each field as 
    an instance variable of the field itself (so you don't have to pass the 
    name of the field to the field constructor).

    """

    def __init__(self, name, bases, dict_):
        type.__init__(self, name, bases, dict_)
        self._fields = {}
        for b in bases:
            dict_ = dict(dict_.items() + b.__dict__.items())
        for k, v in dict_.iteritems():
            if isinstance(v, Field):
                v.name = k
                self._fields[k] = v

class FieldValidationError(ValueError):
    def __init__(self, fieldtype, fullname, msg):
        error="%s '%s' failed validation: %s" % (fieldtype, fullname, msg)
        ValueError.__init__(self, error)

class Field(object):
    """A field in a a Config.

    Instances of Field should be class attributes of Config subclasses:

    class Example(Config):
        myInt = Field(int, "an integer field!", default=0)
    """
    typeWrapper = {
        bool:_Bool, 
        type(None):_None, 
        _None:_None, 
        list:_List, 
        _List:_List,
        Registry:Registry
    }

    def __init__(self, doc, dtype, default=None, check=None, optional=False):
        """Initialize a Field.
        
        dtype ------ Data type for the field.  
        doc -------- Documentation for the field.
        default ---- A default value for the field.
        check ------ A callable to be called with the field value that returns 
                     False if the value is invalid.  More complex inter-field 
                     validation can be written as part of Config validate() 
                     method; this will be ignored if set to None.
        optional --- When False, Config validate() will fail if value is None
        """
        self.dtype = dtype
        if not self.dtype in self.typeWrapper:
            self.typeWrapper[self.dtype] = type(dtype.__name__, (dtype,), {})
        
        self.doc = doc
        self.__doc__ = doc
        self.default = default
        self.check = check
        self.optional = optional

    def rename(self, instance):
        """
        Rename an instance of this field, not the field itself. 
        Only useful for fields which hold sub-configs.
        Fields which hold subconfigs should rename each sub-config with
        the full field name as generated by joinNamePath
        """
        pass

    def validate(self, instance):
        """
        Base validation for any field.
        Ensures that non-optional fields are not None.
        Ensures type correctness
        Ensures that user-provided check function is valid
        Most derived Field types should call Field.validate if they choose
        to re-implement validate
        """
        value = self.__get__(instance)
        fullname = joinNamePath(instance._name, self.name)
        fieldType = type(self).__name__
        if not self.optional and value is None:
            msg = "Required value cannot be None"
            raise FieldValidationError(fieldType, fullname, msg)
        if value and not isinstance(value, self.dtype):
            msg = " Expected type '%s', got '%s'"%(self.dtype, type(value))
            raise FieldValidationError(fieldType, fullname, msg)
        if self.check is not None and not self.check(value):
            msg = "%s is not a valid value"%str(value)
            raise FieldValidationError(fieldType, fullname, msg)

    def save(self, outfile, instance):
        """
        Saves an instance of this field to file.
        This is invoked by the owning config object, and should not be called
        directly
        """
        value = self.__get__(instance)
        fullname = joinNamePath(instance._name, self.name)
        outfile.write("%s=%s\n"%(fullname, repr(value)))
    
    def toDict(self, instance):
        value = self.__get__(instance)
        if isinstance(value, _None):            
            return None
        elif isinstance(value, _Bool):
            return bool(value)
    

    def __get__(self, instance, owner=None):
        if instance is None or not isinstance(instance, Config):
            return self
        else:
            return instance._storage[self.name]

    def __set__(self, instance, value):
        try:
            history = self.__get__(instance).history
        except KeyError, AttributeError:
            history = []
        traceStack = traceback.extract_stack()[:-1]
        history.append((value, traceStack))
        if value is not None:
            wrap = self.typeWrapper[self.dtype](value)
        else:
            wrap = _None()
        wrap.__doc__ = self.doc
        wrap.history = history
        instance._storage[self.name]=wrap

    def __delete__(self, instance):
        self.__set__(instance, None)
   

class Config(object):
    """Base class for control objects.

    A Config object will usually have several Field instances as class 
    attributes; these are used to define most of the base class behavior.  
    Simple derived class should be able to be defined simply by setting those 
    attributes.
    """

    __metaclass__ = ConfigMeta

    def __iter__(self):
        return self._fields.__iter__()

    def keys(self):
        return self._storage.keys()
    def values(self):
        return self._storage.values()
    def items(self):
        return self._storage.items()

    def iteritems(self):
        return self._storage.iteritems()
    def itervalues(self):
        return self.storage.itervalues()
    def iterkeys(self):
        return self.storage.iterkeys()

    def __contains__(self, name):
        return self._storage.__contains__(name)

    def __init__(self, storage=None):
        """Initialize the Config.

        Pure-Python control objects will just use the default constructor, which
        sets up a simple Python dict as the storage for field values.

        Any other object with __getitem__, __setitem__, and __contains__ may be
        used as storage.  This is used to support C++ control objects,
        which will implement a storage interface to C++ data members in SWIG.
        """
        self._name="root"

        self._storage = {}
        #load up defaults
        for field in self._fields.itervalues():
            field.__set__(self, field.default)

        #apply first batch of overrides from the provided storage
        if storage is not None:
            self.override(storage)

    @staticmethod
    def load(filename):
        """
        Construct a new Config object by executing the python code in the 
        given file.

        The python script should construct a Config name root.

        For example:
            from myModule import MyConfig

            root = MyConfig()
            root.myField = 5

        When such a file is loaded, an instance of MyConfig would be returned 
        """
        local = {}
        execfile(filename, {}, local)
        return local['root']
   
 
    def override(self, dict_):
        norm = self._normalizeDict(dict_)
        for k, kv in norm.iteritems():
            target, name, index = self._getTarget(k)
            if kv is None or isinstance(kv, _None):
                kv = None

            if index:
                getattr(target, name)[index] = kv
            else:
                setattr(target, name, kv)

    @staticmethod
    def _normalizeDict(dict_): 
        def hasConflict(target, name):
            for key in target:
                key = repr(key)
                if name.startswith(key) or key.startswith(name):
                    return True        
            return False

        norm = {}
        for k, kv in dict_.iteritems():
            if isinstance(kv, dict):
                sub = _normalizeDict(kv)
            else:
                sub = None
            
            if sub:
                for i, iv in sub.iteritems():
                    fullname = k + "["+repr(i) + "]"
                    if hasConflict(norm, fullname):
                        raise ValueError("ambiguous dict: multiple values for %s"%fullname)
                    norm[fullname]=iv
            else:
                if hasConflict(norm, repr(k)):
                    raise ValueError("ambiguous dict: multiple values for %s"%k)
                norm[k]=kv
        return norm

    def save(self, filename):
        """
        Generates a python script, which, when loaded, reproduces this Config
        """
        tmp = self._name
        self._rename("root")
        try:
            outfile = open(filename, 'w')
            self._save(outfile)
            outfile.close()
        finally:
            self._rename(tmp)
    
    def _save(self, outfile):
        """
        Internal use only. Save this Config to file
        """
        outfile.write("import %s\n"%(type(self).__module__))
        outfile.write("%s=%s()\n"%(self._name, typeString(type(self))))
        for field in self._fields.itervalues():
            field.save(outfile, self)
        
    def toDict(self):
        dict_ = {}
        for name, field in self._fields.iteritems():
            dict_[name] = field.toDict(self)
        return dict_
    def _rename(self, name):
        """
        Internal use only. 
        Rename this Config object to reflect its position in a Config hierarchy
        """
        self._name = name
        for field in self._fields.itervalues():
            field.rename(self)

    def validate(self):
        """
        Validate the Config.

        The base class implementation performs type checks on all fields by 
        calling Field.validate(). 

        Complex single-field validation can be defined by deriving new Field 
        types. As syntactic sugar, some derived Field types are defined in 
        this module which handle recursing into sub-configs 
        (ConfigField, RegistryField, ConfigListField)

        Inter-field relationships should only be checked in derived Config 
        classes after calling this method, and base validation is complete
        """
        for field in self._fields.itervalues():
            field.validate(self)
    


    def _setHistory(self, history):
        """
        Placeholder for adding a checkpoint in a field's history
        """
        for name, field in self._fields.iteritems():
            try:
                fieldHistory = history[name]
                field.setHistory(self, fieldHistory)
            except KeyError:
                pass

    def _getHistory(self):
        """
        Placeholder for retrieving a field's history.

        Field histories are ordered lists of value, traceback pairs
        with oldest information first.
        """
        history = {}
        for name, value in self._storage.iteritems():
            history[name]=value.history
        return history

    history = property(_getHistory, _setHistory)

    def _getTarget(self, fieldname):
        """
        Internal use only.

        Traverse Config hierarchy using a compound field name 
        (e.g. fieldname="foo.bar[5].zed['foo']")
        """
        dot = fieldname.rfind(".")

        target = self
        if dot > 0: 
            try:
                path = fieldname[:dot]
                target = eval("self."+fieldname[:dot])
            except SyntaxError:
                ValueError("Malformed field name '%s'"%fieldname)
            except AttributeError:
                ValueError("Could not find target '%s' in Config %s"%\
                        (fieldname, self._name))
        openBrace = fieldname.find("[", max(dot, 0))
        closeBrace = fieldname.find("]", openBrace)
        if (openBrace >0 and closeBrace < 0) or (openBrace < 0 and closeBrace > 0)\
                or (closeBrace > 0 and closeBrace != len(fieldname) -1) \
                or (closeBrace == openBrace + 1):
            raise ValueError("Malformed field name '%s'"%fieldname)
        elif openBrace > 0:
            name = fieldname[dot+1:openBrace]
            index = eval(fieldname[openBrace+1:closeBrace])
        else:
            name = fieldname[dot+1:]
            index = None

        if not name in target._fields:
            raise ValueError("Config does not include field '%s'"%fieldname)

        return target, name, index

    def __setattr__(self, attr, value):
        if attr in self._fields:
            self._fields[attr].__set__(self, value)
        elif attr == "_name" or attr == "history" or attr == "_storage":
            self.__dict__[attr] = value
        else:
            raise AttributeError("%s has no attribute %s"%(type(self).__name__, attr))

    def __eq__(self, other):
        if isinstance(other, type(self)):
            for name in self._fields:
                if self._storage[name] != other._storage[name]:
                    return False
            return True
        return False
    
    def __ne__(self, other):
        return not self.__eq__(other)

    def __str__(self):
        return str(self.toDict())

    def __repr__(self):
        return repr(self.toDict())
class RangeField(Field):
    """
    Defines a Config Field which allows only a range of values.
    The range is defined by providing min and/or max values.
    If min or max is None, the range will be open in that direction
    If inclusive[Min|Max] is True the range will include the [min|max] value
    """
    def __init__(self, doc, dtype, default=None, optional=False, 
            min=None, max=None, inclusiveMin=True, inclusiveMax=False):

        if min is not None and max is not None and min > max:
            swap(min, max)
        self.min = min
        self.max = max

        self.rangeString =  "%s%s,%s%s" % \
                (("[" if inclusiveMin else "("),
                ("-inf" if self.min is None else self.min),
                ("inf" if self.max is None else self.max),
                ("]" if inclusiveMax else ")"))
        doc += "\n\tValid Range = " + self.rangeString
        if inclusiveMax:
            self.maxCheck = lambda x, y: True if y is None else x <= y
        else:
            self.maxCheck = lambda x, y: True if y is None else x < y
        if inclusiveMin:
            self.minCheck = lambda x, y: True if y is None else x >= y
        else:
            self.minCheck = lambda x, y: True if y is None else x > y
        Field.__init__(self, doc=doc, dtype=dtype, default=default, optional=optional) 

    def validate(self, instance):
        Field.validate(self, instance)
        value = instance._storage[self.name]
        if not self.minCheck(value, self.min) or \
                not self.maxCheck(value, self.max):
            fullname = joinNamePath(instance._name, self.name)
            fieldType = type(self).__name__
            msg = "%s is outside of valid range %s"%(value, self.rangeString)
            raise FieldValidationException(fieldType, fullname, msg)
            
class ChoiceField(Field):
    """
    Defines a Config Field which allows only a set of values
    All allowed must be of the same type.
    Allowed values should be provided as a dict of value, doc string pairs

    """
    def __init__(self, doc, dtype, allowed, default=None, optional=True):
        self.allowed = dict(allowed)
        if optional and None not in self.allowed: 
            self.allowed[None]="Field is optional"

        if len(self.allowed)==0:
            raise ValueError("ChoiceFields must allow at least one choice")
        
        doc += "\nAllowed values:\n"
        for choice, choiceDoc in self.allowed.iteritems():
            if choice is not None and not isinstance(choice, dtype):
                raise ValueError("ChoiceField's allowed choice %s is of type %s. Expected %s"%\
                        (choice, type(choice), dtype))
            doc += "\t%s\t%s\n"%(str(choice), choiceDoc)

        Field.__init__(self, doc=doc, dtype=dtype, default=default, check=None, optional=optional)

    def validate(self, instance):
        Field.validate(self, instance)
        value = self.__get__(instance)
        if value not in self.allowed:
            fullname = joinNamePath(instance._name, self.name)
            fieldType = type(self).__name__
            msg = "Value ('%s') is not allowed"%str(value)
            raise FieldValidationError(fieldType, fullname, msg) 

class ListField(Field):
    """
    Defines a field which is a container of values of type dtype

    If length is not None, then instances of this field must match this length exactly
    If minLength is not None, then instances of the field must be no shorter then minLength
    If maxLength is not None, then instances of the field must be no longer than maxLength
    
    Additionally users can provide two check functions:
    listCheck - used to validate the list as a whole, and
    itemCheck - used to validate each item individually    
    """
    def __init__(self, doc, dtype, default=None, optional=False,
            listCheck=None, itemCheck=None, length=None, minLength=None, maxLength=None):
        Field.__init__(self, doc=doc, dtype=_List, default=default, optional=optional, check=None)
        self.listCheck = listCheck
        self.itemCheck = itemCheck
        self.itemType = dtype
        self.length=length
        self.minLength=minLength
        self.maxLength=maxLength
    
    def validate(self, instance):
        Field.validate(self, instance)
        value = self.__get__(instance) 
        if value is not None:
            fullname = joinNamePath(instance._name, self.name)
            fieldType = type(self).__name__
            lenValue =len(value)
            if self.length is not None and not lenValue == self.length:
                msg = "Required list length=%d, got length=%d"%(self.length, lenValue)                
                raise FieldValidationError(fieldType, fullname, msg)
            elif self.minLength is not None and lenValue < self.minLength:
                msg = "Minimum allowed list length=%d, got length=%d"%(self.minLength, lenValue)                
                raise FieldValidationError(fieldType, fullname, msg)
            elif self.maxLength is not None and lenValue > self.maxLength:
                msg = "Maximum allowed list length=%d, got length=%d"%(self.maxLength, lenValue)                
                raise FieldValidationError(fieldType, fullname, msg)
            elif self.listCheck is not None and not self.listCheck(value):
                msg = "%s is not a valid value"%str(value)
                raise FieldValidationError(fieldType, fullname, msg)
            elif self.itemCheck is not None:
                for i, v in enumerate(value):
                    try:
                        v = self.itemType(v)
                        list.__setitem__(value, i, v)
                    except TypeError:
                        msg="Invalid value %s at position %d"%(str(v), i)
                        raise FieldValidationError(fieldType, fullname, msg)
                        
                    if not self.itemCheck(value[i]):
                        msg="Invalid value %s at position %d"%(str(v), i)
                        raise FieldValidationError(fieldType, fullname, msg)

class ConfigField(Field):
    """
    Defines a field which is itself a Config.

    The behavior of this type of field is much like that of the base Field type.

    Note that dtype must be a subclass of Config.

    If optional=False, and default=None, the field will default to a default-constructed
    instance of dtype

    Additionally, to allow for fewer deep-copies, assigning an instance of ConfigField to dtype istelf,
    rather then an instance of dtype, will in fact reset defaults.

    This means that the argument default can be dtype, rather than an instance of dtype
    """
    def __init__(self, doc, dtype, default=None, check=None, optional=False):        
        if not issubclass(dtype, Config):
            raise TypeError("configType='%s' is not a subclass of Config)"%dtype)
        if default is None and not optional:
            default = dtype
        self.typeWrapper[dtype] = dtype
        Field.__init__(self, doc=doc, dtype=dtype, check=check, default=default, optional=optional)
        
    def __set__(self, instance, value):
        try:
            oldValue = self.__get__(instance)
            history = oldValue.history
        except KeyError, AttributeError:
            oldValue = None
            history = {}
        name=joinNamePath(prefix=instance._name, name=self.name)
        if type(value) == self.dtype:
            storage = value._storage
        elif isinstance(value, dict):
            storage = value
        elif value == self.dtype:
            value = self.dtype()
            storage = value._storage
        elif value:
            raise ValueError("Cannot set ConfigField '%s' to '%s'"%(name, str(value)))
       
        if not value: 
            value = _None()
            value.history = history
            instance._storage[self.name] = value
        else:
            if not oldValue:
                oldValue = self.dtype()
                oldValue._rename(name)
                oldValue.history = history
                instance._storage[self.name] = oldValue
            oldValue.override(storage)

    def rename(self, instance):
        value = self.__get__(instance)
        if value:
            value._rename(joinNamePath(instance._name, self.name))
        
    def save(self, outfile, instance):
        fullname = joinNamePath(instance._name, self.name)
        value = self.__get__(instance)
        if value:
            value._save(outfile)
        else:
            outfile.write("%s=%s\n"%(fullname, str(None)))

    def toDict(self, instance):
        value = self.__get__(instance)
        if value:
            return value.toDict()
        else:
            return None

    def validate(self, instance):
        Field.validate(self, instance)
        value = self.__get__(instance)
        if value:
            value.validate()

class RegistryField(Field):
    """
    Defines a set of name, config pairs, and an "active" choice.
    To set the active choice, assign the field to the name of the choice:

    For example:

      class AaaConfig(Config):
        somefield = Field(int, "...")

      class MyConfig(Config):
        registry = RegistryField("registry", typemap={"A":AaaConfig})
      
      instance = MyConfig()
      instance.registry['AAA'].somefield = 5
      instance.registry = "AAA"
    
    Alternatively, the last line can be written:
      instance.registry.active = "AAA"

    Validation of this field is performed only the "active" choice.
    If active is None and the field is not optional, 

    Registries come in two main flavors: restricted, and unrestricted.
    Restricted registries define all allowed mapping much the same a ChoiceField 
    does. The user provides these mappings in the argument typemap in the Field
    constructor.

    Unrestricted registries allow new entries to be added to the set at runtime.
    This enables plugin style configurations, for which the full set of valid
    configs is not known until runtime.

    Following the previous example:
      class BbbConfig(Config):
        anotherField = Field(float, "...")
      instance.registry["BBB"]=BbbConfig
    This adds another entry to the unrestricted registry, which is an instance of BbbConfig
    
    Registries also allow multiple values of the same type:
      instance.registry["CCC"]=AaaConfig
      instance.registry["BBB"]=AaaConfig

    However, once a name has been associated with a particular type, it cannot be assigned
    to a different type.

    When saving a registry, the entire set is saved, as well as the active selection
    """
    def __init__(self, doc, dtype=Config, default=None, typemap={}, restricted=False, optional=False):
        if len(typemap)==0 and restricted:
            raise ValueError("Cannot instantiate a restricted RegistryField with an empty typemap")
        if not issubclass(dtype, Config):
            raise ValueError("dtype='%s' must be a must be a subclass of Config."%(basetype))

        Field.__init__(self, doc, Registry, default=default, check=None, optional=optional)
        self.typemap = typemap
        self.restricted=restricted
        self.basetype = dtype if dtype is not None else Config
    
    def _getOrMake(self, instance):
        registry = instance._storage.get(self.name)
        if registry is None:
            name = joinNamePath(instance._name, self.name)
            registry = Registry(name, self.basetype, self.typemap, self.restricted)
            registry.__doc__ = self.doc
            instance._storage[self.name] = registry
        return registry


    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        else:
            return self._getOrMake(instance)

    def __set__(self, instance, value):
        registry = self.__get__(instance)
        if value in registry.types:
            registry.name = value
        elif value is None:
            registry.name = None
        else:
            raise KeyError("Unknown key %s in RegistryField %s"%\
                    (repr(value), registry.fullname))
        registry.history.append((value, traceback.extract_stack()[:-1]))

    def rename(self, instance):
        registry = self.__get__(instance)
        for k, v in registry.iteritems():
            fullname = joinNamePath(instance._name, self.name, k)
            v._rename(fullname)

    def validate(self, instance):
        registry = self.__get__(instance)
        if not registry.active and not self.optional:
            fullname = joinNamePath(instance._name, self.name)
            fieldType = type(self).__name__
            msg = "Required field cannot be None"
            raise FieldValidationError(fieldType, fullname, msg)
        elif registry.active:
            registry.active.validate()

    def toDict(self, instance):
        active = self.__get__(instance).active
        if active:
            return active.toDict()
        else:
            return None

    def save(self, outfile, instance):
        registry = self.__get__(instance)
        fullname = registry.fullname
        typesStr = "{"
        for k, t in registry.types.iteritems():
            outfile.write("import %s\n"%(t.__module__))
            typesStr += "'%s':%s, "%(k, typeString(t))
        typesStr += "}"
        outfile.write("%s.types=%s\n"%(fullname, typesStr))
        for v in registry.itervalues():
            v._save(outfile)
        outfile.write("%s=%s\n"%(fullname, repr(registry.name)))
