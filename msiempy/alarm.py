"""Provide alarm management.
"""
import sys
import collections
import datetime
import logging
log = logging.getLogger('msiempy')

from . import NitroDict, NitroList, FilteredQueryList
from .event import EventManager, Event
from .__utils__ import regex_match, convert_to_time_obj, dehexify


__pdoc__={}

class AlarmManager(FilteredQueryList):
    """
    AlarmManager class.
    Interface to query and manage Alarms.
    Inherits from FilteredQueryList.
    """
    def __init__(self, status_filter='all', page_size=None, page_number=None, filters=None, *args, **kwargs):

        """
        Parameters:  
        
        - `status_filter` : status of the alarms to query
        - `page_size` : max number of rows per query, by default takes the value in config `default_rows` option.
        - `page_number` : defaulted to 1.
        - `filters` : [(field, [values]), (field, [values])]
        - `fields` : list of strings. Can be an EsmTriggeredAlarm or an EsmTriggeredAlarmEvent field, or any synonims. See 
        - `*args, **kwargs` : Parameters passed to `msiempy.base.FilteredQueryList.__init__()`
            
        Examples:  
        ```>>>alarm_list=AlarmManager(status_filter='unacknowledged',
            filters=[('sourceIp','^10.*'), ('ruleMessage','Wordpress')]).load_data()```
        """

        super().__init__(*args, **kwargs)

        #Declaring attributes
        self._alarm_filters = list(tuple())
        self._event_filters = list(tuple())
        self._status_filter = str()

        #Setting attributes
        self.status_filter=status_filter
        self.page_size=page_size if page_size is not None else self.requests_size #IGNORE THE CONFIG
        self.requests_size=self.page_size
        self.page_number=page_number if page_number is not None else 1

        #uses the parent filter setter
        #TODO : find a soltuion not to use this stinky tric
        #callign super().filters=filters #https://bugs.python.org/issue14965
        super(self.__class__, self.__class__).filters.__set__(self, filters)

        #Casting all data to Alarms objects, better way to do it ?
        collections.UserList.__init__(self, [Alarm(adict=item) for item in self.data if isinstance(item, (dict, NitroDict))])

    

    @property
    def filters(self):
        """
        Returns the addition of alarm related filters and event related filters.
        """
        return self._alarm_filters + self._event_filters
    
    @property
    def status_filter(self):
        """
        Status of the alarms in the query. `status_filter` is not a filter like other cause it's computed on the SIEM side.
        Other filters are computed locally - Unlike EventManager filters. the status filter of the alarm query. 'acknowledged', 'unacknowledged', 'all', '' or null -> all (default is '').
        You can pass synonims of each status. See `msiempy.alarm.Alarm.POSSIBLE_ALARM_STATUS`.
        """
        return self._status_filter
        """
        for synonims in Alarm.POSSIBLE_ALARM_STATUS :
            if self._status_filter in synonims :
                return synonims[0]"""

    @status_filter.setter
    def status_filter(self, status_filter):
        status_found=False
        if type(status_filter) is str : 
            for synonims in Alarm.POSSIBLE_ALARM_STATUS :
                if status_filter in synonims:
                    self._status_filter=synonims[0]
                    status_found=True
                
        if not status_found:
            raise AttributeError("Illegal value of status filter. The status must be in "+str(Alarm.POSSIBLE_ALARM_STATUS)+' not :'+str(status_filter))

    def add_filter(self, afilter):
        """
            Make sure the filters format is tuple(field, list(values in string)).
            Takes also care of the differents synonims fields can have.
        """

        if isinstance(afilter,str):
            afilter = afilter.split('=',1)
        try:
            values = afilter[1] if isinstance(afilter[1], list) else [afilter[1]]
            values = [str(v) for v in values]
            added=False

            for synonims in Alarm.ALARM_FILTER_FIELDS :
                if afilter[0] in synonims :
                    self._alarm_filters.append((synonims[0], values))
                    added=True

            for synonims in Alarm.ALARM_EVENT_FILTER_FIELDS :
                if afilter[0] in synonims :
                    self._event_filters.append((synonims[0], values))
                    added=True

            #support query related filtering if the filter's field is composed by a table name then a field name separated by a dot.
            if len(afilter[0].split('.')) == 2 :
                self._event_filters.append((afilter[0], values))
                added=True


        except IndexError:
            added = False

        if not added :
            raise AttributeError("Illegal filter field value : "+afilter[0]+". The filter field must be in :"+str(Alarm.ALARM_FILTER_FIELDS + Alarm.ALARM_EVENT_FILTER_FIELDS))

    def clear_filters(self):
        """
        Reset local alarm and event filters.
        """
        self._alarm_filters = list(tuple())
        self._event_filters = list(tuple())

    def load_data(self, *args, **kwargs):
        """
        Shortcut for `msiempy.alarm.AlarmManager.qry_load_data`.  
        #TODO : Implement paging
        Parameters :
        - `*args, **kwargs` : Same as `msiempy.alarm.AlarmManager.qry_load_data`

        Returns : `msiempy.alarm.AlarmManager`
        """
        return AlarmManager(alist=super().load_data(*args, **kwargs))

    def qry_load_data(self, workers, no_detailed_filter=False, use_query=False, extra_fields=[], **kwargs):
        """
        Method that loads the data :
            -> Fetch the list of alarms and load alarms details  
            -> Filter depending on alarms related filters  
            -> Load the events details  
            -> Filter depending on event related filters  

        Parameters :  

        - `workers` : Number of asynchronous workers  
        - `no_detailed_filter` : Don't load detailed alarms and events infos, you can only filter based on `msiempy.alarm.Alarm.ALARM_FILTER_FIELDS` values  
        - `use_query` : Uses the query module to retreive common event data. Only works with SIEM v 11.2.1 or greater  
        - `extra_fields` :  Only when `use_query=True`. Additionnal event fields to load in the query. See : `msiempy.event.EventManager`  
        - `**kwargs` : Not used

        Returns : `tuple` : ( `msiempy.alarm.AlarmManager`, Status of the query : `completed` )

        """

        if self.time_range == 'CUSTOM' :
            no_filtered_alarms=self.nitro.request(
                'get_alarms_custom_time',
                time_range=self.time_range,
                start_time=self.start_time,
                end_time=self.end_time,
                status=self.status_filter,
                page_size=self.page_size,
                page_number=self.page_number
                )

        else :
            no_filtered_alarms=self.nitro.request(
                'get_alarms',
                time_range=self.time_range,
                status=self.status_filter,
                page_size=self.page_size,
                page_number=self.page_number
                )
                
        alarm_based_filtered = AlarmManager(alist=[a for a in no_filtered_alarms if self._alarm_match(a)])

        if not no_detailed_filter :

            log.info("Getting alarms infos...")
            alarm_detailed = self.perform(Alarm.load_details,
                list(alarm_based_filtered),
                asynch=True,
                progress=True,
                workers=workers)

            log.info("Getting events infos...")
            event_detailed = self.perform(Alarm.load_events, 
                list(alarm_detailed),
                func_args=dict(use_query=use_query, extra_fields=extra_fields),
                asynch=True, 
                progress=True, 
                workers=workers)

            filtered_alarms = AlarmManager(alist=[a for a in event_detailed if self._event_match(a)])

        else :
            filtered_alarms = alarm_based_filtered

        log.info(str(len(filtered_alarms)) + " alarms matching your filter(s)")

        return (( filtered_alarms , len(no_filtered_alarms)<self.page_size ))

    def _alarm_match(self, alarm):
        """
        Internal filter method that is going to return True if the passed alarm match any alarm related filters.
        """
        match=True
        for alarm_filter in self._alarm_filters :
            match=False
            if alarm_filter[0] not in alarm:
                break
            value = str(alarm[alarm_filter[0]]) #Can only match strings
            for filter_value in alarm_filter[1]:
                if regex_match(filter_value.lower(), value.lower()):
                    match=True
                    break
            if not match :
                break
        return match
        
    def _event_match(self, alarm):
        """
        Internal filter method that is going to return True if the passed alarm match any event related filters.
        """
        match=True
        for event_filter in self._event_filters :
            match=False
            if event_filter[0] not in alarm['events'][0]:
                break
            value = str(alarm['events'][0][event_filter[0]])
            for filter_value in event_filter[1]:
                if regex_match(filter_value.lower(), value.lower()) :
                    match=True
                    break
            if not match :
                break
        return match
        
class Alarm(NitroDict):
    """
    Dict keys :  
        - `id` : The ID of the triggered alarm  
        - `summary`  : The summary of the triggered alarm  
        - `assignee` : The assignee for this triggered alarm  
        - `severity` : The severity for this triggered alarm  
        - `triggeredDate` : The date this alarm was triggered  
        - `acknowledgedDate` : The date this triggered alarm was acknowledged  
        - `acknowledgedUsername` : The user that acknowledged this triggered alarm  
        - `alarmName` : The name of the alarm that was triggered  
        - `events` : The events for this user  
        - And others...  
    
    """

    """@property
    def status(self):
        return('acknowledged' 
            if ( (len(self.data['acknowledgedDate'])>0) 
                and (len(self.data['acknowledgedUsername'])>0))
            else 'unacknowledged')"""

    POSSIBLE_ALARM_STATUS=[
        ['acknowledged', 'ack',],
        ['unacknowledged', 'unack',],
        ['', 'all', 'both']
    ]
    __pdoc__['Alarm.POSSIBLE_ALARM_STATUS']="""Possible alarm statuses : ```%(statuses)s```""" % dict(statuses=', '.join([ '/'.join(synonims) for synonims in POSSIBLE_ALARM_STATUS]))

    ALARM_FILTER_FIELDS = [('id',),
    ('summary','sum'),
    ('assignee','user'),
    ('severity','sever'),
    ('triggeredDate','trigdate'),
    ('acknowledgedDate','ackdate'),
    ('acknowledgedUsername','ackuser'),
    ('alarmName','name'),
    ]
    __pdoc__['Alarm.ALARM_FILTER_FIELDS']="""Possible alarm related fields usable in a filter : ```%(fields)s```""" % dict(fields=', '.join([ '/'.join(synonims) for synonims in ALARM_FILTER_FIELDS]))

    ALARM_EVENT_FILTER_FIELDS=[
    ("ruleName",),
    ("srcIp",),
    ("destIp",),
    ("protocol",),
    ("lastTime",),
    ("subtype",),
    ("destPort",),
    ("destMac",),
    ("srcMac",),
    ("srcPort",),
    ("deviceName",),
    ("sigId",),
    ("normId",),
    ("srcUser",),
    ("destUser",),
    ("normMessage",),
    ("normDesc",),
    ("host",),
    ("domain",),
    ("ipsId",),
    ]
    __pdoc__['Alarm.ALARM_EVENT_FILTER_FIELDS']="""Possible event related fields usable in a filter : ```%(fields)s```""" % dict(fields=', '.join([ '/'.join(synonims) for synonims in ALARM_EVENT_FILTER_FIELDS]))

    ALARM_DEFAULT_FIELDS=['id','alarmName', 'summary','triggeredDate', 'acknowledgedUsername']
    __pdoc__['Alarm.ALARM_DEFAULT_FIELDS']="""Defaulfs fields : `%(fields)s` 
    (not used , may be for printing with `msiempy.NitroList.get_text(fields=msiempy.alarm.ALARM_DEFAULT_FIELDS)`)""" % dict(fields=', '.join(ALARM_DEFAULT_FIELDS))

    def __init__(self, *arg, **kwargs):
        """Creates a empty Alarm.
        """
        super().__init__(*arg, **kwargs)

        #Keep the id in the dict when instanciating an Alarm directly from its id.
        if 'id' in kwargs :
            self.data['id'] = {'value':str(kwargs['id'])}

    def acknowledge(self):
        """Mark the alarm as acknowledged.
        """
        self.nitro.request('ack_alarms', ids=[self.data['id']['value']])

    def unacknowledge(self):
        """Mark the alarm as unacknowledge.    
        """
        self.nitro.request('unack_alarms', ids=[self.data['id']['value']])

    def delete(self):
        """Destructive action!
        Delete the alarm.
        """
        self.nitro.request('delete_alarms', ids=[self.data['id']['value']])

    def ceate_case(self):
        """Not implemented : TODO
        """
        raise NotImplementedError()

    def load_details(self):
        """Update the alarm with detailled data loaded from the SIEM.
        """
        the_id = self.data['id']['value']
        self.data.update(self.data_from_id(the_id))
        self.data['id']['value']=the_id

        return self

    def refresh(self):
        """Update the alarm with detailled data loaded from the SIEM. Concrete NitroObject method.
        """        
        self.load_details()

    def load_events(self, use_query=False, extra_fields=[]):
        """
        Retreive the genuine Event object from an Alarm.
        Warning : This method will only load the details of the first triggering event.  
        Parameters:  
        - `use_query` : Uses the query module to retreive common event data. Only works with SIEM v 11.2.x.  
        - `extra_fields` : Only when `use_query=True`. Additionnal event fields to load in the query. See : `msiempy.event.EventManager`  
        """
        if isinstance(self.data['events'], str):

            #Retreive the alert id from the event's string
            events_data=self.data['events'].split('|')
            the_id = events_data[0]+'|'+events_data[1]

            #instanciate the event
            the_first_event=Event()
            the_first_event.data = Event().data_from_id(id=the_id, use_query=use_query, extra_fields=extra_fields)

            #set it as the only item of the event list
            self.data['events']= [ the_first_event ]

        else:
            log.warning('The alarm {} ({}) has no events associated'.format(self.data['alarmName'], self.data['triggeredDate']))
            self.data['events']= [ Event() ]

        return self

    def map_alarm_int_fields(self, alarm_details):
        for key, val in alarm_details.items():
            if alarm_details[key] == key:
                alarm_details[key] = None
            elif alarm_details[key] in ['f', 'F']:
                alarm_details[key] = False
            elif alarm_details[key] in ['t', 'T']:
                alarm_details[key] = True
            else:
                alarm_details[key] = val

        new_alarm = {}
        new_alarm['filters'] = alarm_details.get('FILTERS') or None
        new_alarm['queryId'] = alarm_details.get('QID') or None
        new_alarm['alretRateMin'] = alarm_details.get('ARM') or None
        new_alarm['alertRateCount'] = alarm_details.get('ARC') or None
        new_alarm['percentAbove'] = alarm_details.get('PCTA') or None
        new_alarm['percentBelow'] = alarm_details.get('PCTB') or None
        new_alarm['offsetMinutes'] = alarm_details.get('OFFSETMIN') or None
        new_alarm['maximumConditionTriggerFrequency'] = alarm_details.get('TIMEF') or None
        new_alarm['useWatchlist'] = alarm_details.get('USEW') or None
        new_alarm['matchField'] = alarm_details.get('MFLD') or None
        new_alarm['matchValue'] = alarm_details.get('MWAL') or None
        #new_alarm['healthMonalarm_details'] = alarm_details.get('HMS') or None
        new_alarm['assigneeId'] = alarm_details.get('ASNID') or None
        new_alarm['escalatedDate'] = alarm_details.get('ESCDATE') or None
        new_alarm['caseId'] = alarm_details.get('CASEID') or None
        new_alarm['caseName'] = alarm_details.get('CASENAME') or None
        new_alarm['iocName'] = alarm_details.get('IOCNAME') or None
        new_alarm['iocId'] = alarm_details.get('IOCID') or None
        new_alarm['description'] = alarm_details.get('DESC') or None
        new_alarm['actions'] = alarm_details.get('ACTIONS') or None
        new_alarm['events'] = alarm_details.get('EVENTS') or None
        return new_alarm
                                                                                       
    def data_from_id(self, id):
        """

        """
        alarms = self.nitro.request('get_alarm_details_int', id=str(id))
        alarms = {key: dehexify(val).replace('\n', '|') for key, val in alarms.items()} #this line is skechy
        alarms = self.map_alarm_int_fields(alarms)        
        return alarms

    """
    def _hasID(self):
        try :
            if self.data['id']['value'] == 0 :
                return False
            else :
                return True
        
        except KeyError :
            return False"""

