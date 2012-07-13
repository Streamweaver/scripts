import urllib
import urllib2
from xml.dom import minidom
from datetime import datetime, timedelta
from collections import defaultdict

from local_settings import API_TOKEN

ESTIMATE_TYPES = ['unscheduled', 'started', 'finished', 'rejected', 'delivered', 'accepted']

def get_data(dom, name, default=None):
    """
    Grabs a particular value from the first element in a dom and returns default if none.
    """
    try:  # if this element doesn't exist return the default value
        return dom.getElementsByTagName(name)[0].firstChild.data
    except IndexError: # Return if element exists and is blank
        return default
    except AttributeError: # Return if element does not exist.
        return default

def get_datetime_data(dom, name, default=None):
    """
    Grabs the text value from an item in the dom and returns a datetime object representation of that value.
    """
    raw_data = get_data(dom, name, default)
    try:
        return datetime.strptime(raw_data, '%Y/%m/%d %H:%M:%S UTC')
    except ValueError: # Return this if can't be converted to date.
        return default
    except TypeError: # Return this is no data existed.
        return default

def get_int_data(dom, name, default=0):
    """
    Returns the value of first name element in dom as an int.
    """
    try:
        return int(get_data(dom, name, default))
    except TypeError:
        return default

def get_float_data(dom, name, default=0.0):
    """
    Returns the value of first name element in dom as a float.
    """
    try:
        return float(get_data(dom, name, default))
    except TypeError:
        return default

def get_boolean_data(dom, name, default=False):
    """
    Returns the value of first name element in dom as a boolean
    """
    values = ['true', 'True'] # possible options in the return.
    try:
        if get_data(dom, name, default) in values:
            return True
        return False
    except TypeError:  # Doubt we'll ever hit this but just in case.
        return default


class PivotalAPI(object):

    DEFAULT_API_URL = "http://www.pivotaltracker.com/services/v3/projects"

    def __init__(self, token, api_url=None):
        self.token = token
        self.return_dom = None
        self.set_api_url(api_url)

    def set_api_url(self, api_url):
        if not api_url:
            self.api_url = self.DEFAULT_API_URL
        else:
            self.api_url = api_url

    def _query_api(self, url, params=None):
        """
        Executes a query on the API and returns a parsed dom.
        """
        if params:
            url = "%s?%s" % (url, self._build_storyfilter(params))
        req = urllib2.Request(url, None, {'X-TrackerToken': self.token}) # Make a simple API call using our token.
        response = urllib2.urlopen(req)
        return minidom.parseString(response.read()) # Parse the return into a dom.

    def _build_storyfilter(self, filter_params):
        """
            This method builds the story filter parameters for the API Query url for the Pivotal Tracker API.
            See documentation at https://www.pivotaltracker.com/help/api?version=v3#get_stories_by_filter

            :param filter_params: dict of key value filter paramaters to convert and format.
        """
        story_params = ['%s:%s' % (key, value) for key, value in filter_params.items()]
        query_params = {'filter': " ".join(story_params), "limit": 100}
        return urllib.urlencode(query_params)

class Portfolio(PivotalAPI):

    def __init__(self, token, api_url=None):
        """
        A py thonic representation of a pivotal tracker project list return from the API.
        Request an API key from your account profile at https://www.pivotaltracker.com/profile

        :param token:  API token for a specific user account.
        :param api_url: if the default api URL needs overriding.
        """
        super(Portfolio, self).__init__(token, api_url)
        self.project_list = []
        self.return_dom = self._query_api(self.api_url) # Get the dom from the API
        self._parse_projects()  # Parse the projects from the return into Project objects.

    def _parse_projects(self):
        """
        Makes a call to the API to pull back a list of projects and initalize them in memory.
        """
        for project_xml in self.return_dom.getElementsByTagName('project'):
            self.project_list.append(Project(self.token, project_xml))

    def get_work_info(self, start_date=None):
        """
        Aggregates some basic cross project iteration information.

        :param start_date: datetime for the start of an interation to measure.
        """
        if not start_date:
            start_date = datetime.now() # default to current

        data = defaultdict(int)
        for project in self.project_list:
            for itr in project.iteration_list:
                if itr.start < start_date and itr.finish > start_date:
                    for k, v in itr.work_profile().items():
                        data[k] += v
                    data['chores'] += itr.count_story('chore')
                    data['bugs'] += itr.count_story('bug')
        return data

class Project(PivotalAPI):

    BASE_API_URL = 'http://www.pivotaltracker.com/services/v3/projects/'

    def __init__(self, token, project_dom):
        """
        Pythonic represenation of a project return from the PivotalTracker API.

        :param project_dom:  XML return from the API for an individual project.
        """
        super(Project, self).__init__(token)
        self.name = get_data(project_dom, 'name')
        self.id = get_int_data(project_dom, 'id')
        self.iteration_length = get_data(project_dom, 'iteration_length')
        self.week_start_day = get_data(project_dom, 'week_start_day')
        self.point_scale = get_data(project_dom, 'point_scale')
        self.current_velocity = get_int_data(project_dom, 'current_velocity')
        self.last_activity_at = get_datetime_data(project_dom, 'last_activity_at')
        self.first_iteration_start_time = get_datetime_data(project_dom, 'first_iteration_start_time')
        self.set_api_url("%s/%s" % (self.api_url, self.id))
        self.story_list = []
        self.iteration_list = []
        self.get_iteration_data()
        self._parse_stories()

    def _parse_stories(self):
        """
        Makes a call to the API to pull back project information from the API.

        See additional information about filters for stories in the API documentation.
        https://www.pivotaltracker.com/help/api?version=v3#get_stories_by_filter

        done tickes need the filter option 'includedone:true'
        """
        stories_url = "%s/stories" % self.api_url
        story_params = {
            'includedone': 'true',
        }
        story_list_dom = self._query_api(stories_url, params=story_params)
        for story_xml in story_list_dom.getElementsByTagName('story'):
            self.story_list.append(Story(self.token, story_xml))

    def old_get_iteration_data(self, start_date, end_date):
        """
        Sums the total of stories closed in a specific date range and returns that value.
        """
        data = defaultdict(int) # I'm returning Int data here
        for story in self.story_list:
            if story.estimate and story.updated_at >= start_date and story.updated_at <= end_date:
                    data[story.current_state] += int(story.estimate)
        return data

    def get_iteration_data(self):
        url = "%s/iterations" % self.api_url
        iterations_dom = self._query_api(url)
        for iteration_xml in iterations_dom.getElementsByTagName('iteration'):
            self.iteration_list.append(Iteration(self.token, iteration_xml))

class Story(PivotalAPI):

    """lowercased current state options for stories used in pivotal tracker"""
    STATE_OPTIONS = ('unscheduled', 'started', 'finished', 'rejected', 'delivered', 'accepted')

    def __init__(self, token, story_dom):
            """
            Pythonic representation of a user story.

            :param story_dom: XML return for an individual story.
            """
            super(Story, self).__init__(token)
            self.id = get_int_data(story_dom, 'id')
            self.story_type = get_data(story_dom, 'story_type')
            self.url = get_data(story_dom, 'url')
            self.current_state = get_data(story_dom, 'current_state')
            self.name = get_data(story_dom, 'name')
            self.requested_by = get_data(story_dom, 'requested_by')
            self.created_at = get_datetime_data(story_dom, 'created_at')
            self.updated_at = get_datetime_data(story_dom, 'updated_at')
            self.description = get_data(story_dom, 'description')
            self.owned_by = get_data(story_dom, 'owned_by')
            self.estimate = get_int_data(story_dom, 'estimate')

class Iteration(PivotalAPI):

    def __init__(self, token, iteration_dom, api_url=None):
        """
        Pythonic represenation of an Iteration return from the PT API.
        See documentation at https://www.pivotaltracker.com/help/api?version=v3#get_iterations
        """
        super(Iteration, self).__init__(token, api_url)
        # self.set_api_url("%s/iterations" % self.api_url)
        self.dom = iteration_dom
        self.id = get_int_data(iteration_dom, 'id')
        self.number = get_int_data(iteration_dom, 'number')
        self.start = get_datetime_data(iteration_dom, 'start')
        self.finish = get_datetime_data(iteration_dom, 'finish')
        self.team_strength = get_int_data(iteration_dom, 'team_strength')
        self.story_list = self._parse_stories(token, iteration_dom)
        self.owner_list = self._parse_owners(self.story_list)

    def _parse_stories(self, token, iteration_dom):
        """
        Parses the stories tag into indovidual story objects from the return.
        """
        return [Story(token, story_dom) for story_dom in iteration_dom.getElementsByTagName('story')]

    def _parse_owners(self, story_list):
        """
        Aggregates simple information about owners of various stories and the point totals.
        """
        owner_list = {story.owned_by: defaultdict(int) for story in story_list}
        for story in story_list:
            owner_list[story.owned_by][story.current_state] += story.estimate
        return owner_list

    def work_profile(self):
        """
        Returns a summary of the point breakdown, bugs and chores.
        """
        data = defaultdict(int)
        for story in self.story_list:
            if story.story_type == 'feature':
                data[story.current_state] += story.estimate
        return data

    def owner_profile(self):
        """
        Returns a count of who finished what points
        """
        data = defaultdict(int)
        for story in self.story_list:
            if story.story_type == 'feature':
                data[story.owned_by] += story.estimate
        return data

    def count_story(self, type):
        """
        Returns the count of a partular type of story.
        """
        count = 0
        for story in self.story_list:
            if story.story_type == type:
                count += 1
        return count

    def print_xml(self):
        print self.dom.toprettyxml()

    def print_structure(self):
        for node in self.dom.childNodes:
            print node

class PortfolioManager:

    def __init__(self, portfolio):
        """
        This class helps report on, aggregate and produce information about our collection of Projects.

        :param portfolio:  A portfolio object contianing all the other information.
        """
        self.portfolio = portfolio

    def get_projects(self):
        """
        Returns the portfolio project list.
        """
        return self.portfolio.project_list

    def get_iteration_data(self, start_date, end_date):
        """
        Returns information about
        """
        data = {}
        for project in self.get_projects():
            data[project.name] = project.get_iteration_data(start_date, end_date)
        return data

# GET TO THE MAIN CLASS AND RUN IT

def main():
    portfolio = PortfolioManager(Portfolio(API_TOKEN))
    #
    current = datetime.now() - timedelta(days=2)
    last = current - timedelta(days=14)
    #print end
    #print start
    #for project, data in portfolio.get_iteration_data(start, end).items():
    #    print project
    #    for k, v in data.items():
    #        print "   ", k, v

    for project in portfolio.get_projects():
        for itr in project.iteration_list:
            if itr.start > current or itr.finish < current:
                continue
            print "%s: %s - %s" % (project.name, itr.number, itr.start)
            data = itr.work_profile()
            for k, v in data.items():
                print "   %s: %s" % (k, v)
            print "   chores: %s" % itr.count_story('chore')
            print "   bugs: %s" % itr.count_story('bug')
    print "####### ALL PROJECTS ##############"
    for k, v in portfolio.portfolio.get_work_info(start_date=current).items():
        print "%s: %s" % (k, v)

if __name__ == '__main__':
    main()
