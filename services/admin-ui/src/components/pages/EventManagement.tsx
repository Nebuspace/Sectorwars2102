import React, { useState, useEffect } from 'react';
import PageHeader from '../ui/PageHeader';
import { api } from '../../utils/auth';
import './event-management.css';

interface GameEvent {
  id: string;
  title: string;
  description: string;
  event_type: 'economic' | 'combat' | 'exploration' | 'seasonal' | 'emergency';
  status: 'scheduled' | 'active' | 'completed' | 'cancelled';
  start_time: string;
  end_time: string;
  affected_regions: string[];
  effects: EventEffect[];
  participation_count: number;
  rewards_distributed: number;
  created_by: string;
  created_at: string;
}

interface EventEffect {
  type: 'price_modifier' | 'spawn_rate' | 'experience_bonus' | 'resource_bonus';
  target: string;
  modifier: number;
  duration_hours: number;
}

interface EventTemplate {
  id: string;
  name: string;
  description: string;
  event_type: string;
  default_effects: EventEffect[];
  duration_hours: number;
}

interface EventStats {
  total_events: number;
  active_events: number;
  scheduled_events: number;
  total_participants: number;
  rewards_distributed: number;
}

const EventManagement: React.FC = () => {
  const [events, setEvents] = useState<GameEvent[]>([]);
  const [selectedEvent, setSelectedEvent] = useState<GameEvent | null>(null);
  const [eventStats, setEventStats] = useState<EventStats | null>(null);
  const [templates, setTemplates] = useState<EventTemplate[]>([]);
  const [templatesError, setTemplatesError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [typeFilter, setTypeFilter] = useState<string>('all');
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);

  // New event form state
  const [newEvent, setNewEvent] = useState({
    title: '',
    description: '',
    event_type: 'economic' as const,
    start_time: '',
    end_time: '',
    affected_regions: [] as string[],
    effects: [] as EventEffect[]
  });

  useEffect(() => {
    fetchEventData();
    fetchEventTemplates();
  }, [page, statusFilter, typeFilter]);

  const fetchEventData = async () => {
    try {
      setLoading(true);
      setError(null);
      
      // Fetch events with current filters
      const response = await api.get('/api/v1/admin/events/', {
        params: {
          page,
          limit: 20,
          status_filter: statusFilter !== 'all' ? statusFilter : undefined,
          type_filter: typeFilter !== 'all' ? typeFilter : undefined,
          search_term: searchTerm || undefined
        }
      });
      
      const data = response.data as any;
      setEvents(Array.isArray(data?.events) ? data.events : []);
      setTotalPages(typeof data?.total_pages === 'number' ? data.total_pages : 1);
      
      // Fetch event statistics
      const statsResponse = await api.get('/api/v1/admin/events/stats');
      setEventStats(statsResponse.data as EventStats);

    } catch (error) {
      console.error('Error fetching event data:', error);
      setError(error instanceof Error ? error.message : 'Failed to fetch event data');
      setEvents([]);
      setEventStats(null);
    } finally {
      setLoading(false);
    }
  };

  const fetchEventTemplates = async () => {
    try {
      setTemplatesError(null);
      const response = await api.get('/api/v1/admin/events/templates');
      setTemplates(response.data as EventTemplate[]);
    } catch (error) {
      console.error('Error fetching event templates:', error);
      setTemplates([]);
      setTemplatesError(error instanceof Error ? error.message : 'Failed to load event templates');
    }
  };

  const handleCreateEvent = async () => {
    try {
      const response = await api.post('/api/v1/admin/events/', newEvent);

      if (response.status === 200 || response.status === 201) {
        await fetchEventData();
        setShowCreateForm(false);
        setNewEvent({
          title: '',
          description: '',
          event_type: 'economic',
          start_time: '',
          end_time: '',
          affected_regions: [],
          effects: []
        });
      } else {
        alert('Failed to create event');
      }
    } catch (error) {
      console.error('Error creating event:', error);
      alert('Error creating event');
    }
  };

  const handleCancelEvent = async (eventId: string) => {
    if (!confirm('Are you sure you want to cancel this event?')) {
      return;
    }

    try {
      const response = await api.post(`/api/v1/admin/events/${eventId}/deactivate`);

      if (response.status === 200) {
        await fetchEventData();
      } else {
        alert('Failed to cancel event');
      }
    } catch (error) {
      console.error('Error cancelling event:', error);
      alert('Error cancelling event');
    }
  };

  const handleActivateEvent = async (eventId: string) => {
    try {
      const response = await api.post(`/api/v1/admin/events/${eventId}/activate`);

      if (response.status === 200) {
        await fetchEventData();
      } else {
        alert('Failed to activate event');
      }
    } catch (error) {
      console.error('Error activating event:', error);
      alert('Error activating event');
    }
  };

  const applyTemplate = (template: EventTemplate) => {
    setNewEvent({
      ...newEvent,
      title: template.name,
      description: template.description,
      event_type: template.event_type as any,
      effects: [...template.default_effects],
      end_time: new Date(Date.now() + template.duration_hours * 60 * 60 * 1000).toISOString().slice(0, 16)
    });
  };

  const filteredEvents = events.filter(event => {
    const matchesSearch = event.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
                         event.description.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesStatus = statusFilter === 'all' || event.status === statusFilter;
    const matchesType = typeFilter === 'all' || event.event_type === typeFilter;
    
    return matchesSearch && matchesStatus && matchesType;
  });

  const formatDateTime = (dateString: string) => {
    return new Date(dateString).toLocaleString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'active': return 'green';
      case 'scheduled': return 'blue';
      case 'completed': return 'gray';
      case 'cancelled': return 'red';
      default: return 'gray';
    }
  };

  if (loading) {
    return (
      <div className="event-management">
        <PageHeader 
          title="Event Management" 
          subtitle="Create and manage dynamic game events"
        />
        <div className="loading-spinner">Loading event data...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="event-management">
        <PageHeader 
          title="Event Management" 
          subtitle="Create and manage dynamic game events"
        />
        <div className="error-message">
          <h3>Error Loading Event Data</h3>
          <p>{error}</p>
          <button onClick={fetchEventData} className="retry-button">
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="event-management">
      <PageHeader 
        title="Event Management" 
        subtitle="Create and manage dynamic game events"
      />
      
      {/* Event Statistics */}
      {eventStats && (
        <div className="event-stats-grid">
          <div className="event-stat-card">
            <h3 className="event-stat-title">Total Events</h3>
            <span className="event-stat-value">{eventStats.total_events.toLocaleString()}</span>
          </div>
          <div className="event-stat-card">
            <h3 className="event-stat-title">Active</h3>
            <span className="event-stat-value">{eventStats.active_events.toLocaleString()}</span>
          </div>
          <div className="event-stat-card">
            <h3 className="event-stat-title">Scheduled</h3>
            <span className="event-stat-value">{eventStats.scheduled_events.toLocaleString()}</span>
          </div>
          <div className="event-stat-card">
            <h3 className="event-stat-title">Participants</h3>
            <span className="event-stat-value">{eventStats.total_participants.toLocaleString()}</span>
          </div>
          <div className="event-stat-card">
            <h3 className="event-stat-title">Rewards Given</h3>
            <span className="event-stat-value">{eventStats.rewards_distributed.toLocaleString()}</span>
          </div>
        </div>
      )}

      <div className="events-content">
        {/* Events Controls */}
        <div className="events-controls">
          <button 
            onClick={() => setShowCreateForm(!showCreateForm)}
            className="create-event-btn"
          >
            {showCreateForm ? 'Cancel' : 'Create Event'}
          </button>
          
          <div className="search-bar">
            <input
              type="text"
              placeholder="Search events..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>
          
          <div className="filter-controls">
            <select 
              value={statusFilter} 
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="all">All Status</option>
              <option value="scheduled">Scheduled</option>
              <option value="active">Active</option>
              <option value="completed">Completed</option>
              <option value="cancelled">Cancelled</option>
            </select>
            
            <select 
              value={typeFilter} 
              onChange={(e) => setTypeFilter(e.target.value)}
            >
              <option value="all">All Types</option>
              <option value="economic">Economic</option>
              <option value="combat">Combat</option>
              <option value="exploration">Exploration</option>
              <option value="seasonal">Seasonal</option>
              <option value="emergency">Emergency</option>
            </select>
          </div>
        </div>

        {/* Create Event Form */}
        {showCreateForm && (
          <div className="create-event-form">
            <h3>Create New Event</h3>
            
            {/* Event Templates */}
            <div className="templates-section">
              <h4>Quick Templates</h4>
              {templatesError ? (
                <div className="templates-error">
                  <p>Unable to load event templates: {templatesError}</p>
                  <button onClick={fetchEventTemplates} className="retry-button">
                    Retry
                  </button>
                </div>
              ) : templates.length === 0 ? (
                <p className="templates-empty">No event templates available.</p>
              ) : (
                <div className="templates-grid">
                  {templates.map(template => (
                    <div
                      key={template.id}
                      className="template-card"
                      onClick={() => applyTemplate(template)}
                    >
                      <h5>{template.name}</h5>
                      <p>{template.description}</p>
                      <span className="template-type">{template.event_type}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Event Form */}
            <div className="form-grid">
              <div className="form-group">
                <label>Event Title</label>
                <input
                  type="text"
                  value={newEvent.title}
                  onChange={(e) => setNewEvent({...newEvent, title: e.target.value})}
                  placeholder="Enter event title"
                />
              </div>
              
              <div className="form-group">
                <label>Event Type</label>
                <select
                  value={newEvent.event_type}
                  onChange={(e) => setNewEvent({...newEvent, event_type: e.target.value as any})}
                >
                  <option value="economic">Economic</option>
                  <option value="combat">Combat</option>
                  <option value="exploration">Exploration</option>
                  <option value="seasonal">Seasonal</option>
                  <option value="emergency">Emergency</option>
                </select>
              </div>
              
              <div className="form-group">
                <label>Start Time</label>
                <input
                  type="datetime-local"
                  value={newEvent.start_time}
                  onChange={(e) => setNewEvent({...newEvent, start_time: e.target.value})}
                />
              </div>
              
              <div className="form-group">
                <label>End Time</label>
                <input
                  type="datetime-local"
                  value={newEvent.end_time}
                  onChange={(e) => setNewEvent({...newEvent, end_time: e.target.value})}
                />
              </div>
            </div>
            
            <div className="form-group">
              <label>Description</label>
              <textarea
                value={newEvent.description}
                onChange={(e) => setNewEvent({...newEvent, description: e.target.value})}
                placeholder="Enter event description"
                rows={3}
              />
            </div>
            
            <div className="form-actions">
              <button onClick={handleCreateEvent} className="create-btn">
                Create Event
              </button>
              <button 
                onClick={() => setShowCreateForm(false)} 
                className="cancel-btn"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Events List */}
        <div className="events-list">
          <h3>Events ({filteredEvents.length})</h3>
          
          {filteredEvents.length > 0 ? (
            <div className="events-table">
              <table>
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Type</th>
                    <th>Status</th>
                    <th>Start Time</th>
                    <th>End Time</th>
                    <th>Participants</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredEvents.map(event => (
                    <tr 
                      key={event.id}
                      onClick={() => setSelectedEvent(event)}
                      className={selectedEvent?.id === event.id ? 'selected' : ''}
                    >
                      <td className="event-title">{event.title}</td>
                      <td>
                        <span className={`event-type ${event.event_type}`}>
                          {event.event_type}
                        </span>
                      </td>
                      <td>
                        <span 
                          className="status" 
                          style={{ color: getStatusColor(event.status) }}
                        >
                          {event.status}
                        </span>
                      </td>
                      <td>{formatDateTime(event.start_time)}</td>
                      <td>{formatDateTime(event.end_time)}</td>
                      <td>{event.participation_count}</td>
                      <td>
                        {event.status === 'scheduled' && (
                          <button 
                            onClick={(e) => {
                              e.stopPropagation();
                              handleActivateEvent(event.id);
                            }}
                            className="activate-btn"
                          >
                            Activate
                          </button>
                        )}
                        {(event.status === 'scheduled' || event.status === 'active') && (
                          <button 
                            onClick={(e) => {
                              e.stopPropagation();
                              handleCancelEvent(event.id);
                            }}
                            className="cancel-btn"
                          >
                            Cancel
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="no-events">
              <p>No events found matching your criteria.</p>
            </div>
          )}
        </div>

        {/* Event Details */}
        {selectedEvent && (
          <div className="event-details">
            <h3>{selectedEvent.title}</h3>
            <div className="event-meta">
              <p><strong>Type:</strong> {selectedEvent.event_type}</p>
              <p><strong>Status:</strong> 
                <span 
                  className="status" 
                  style={{ color: getStatusColor(selectedEvent.status) }}
                >
                  {selectedEvent.status}
                </span>
              </p>
              <p><strong>Start:</strong> {formatDateTime(selectedEvent.start_time)}</p>
              <p><strong>End:</strong> {formatDateTime(selectedEvent.end_time)}</p>
              <p><strong>Participants:</strong> {selectedEvent.participation_count}</p>
              <p><strong>Rewards Distributed:</strong> {selectedEvent.rewards_distributed.toLocaleString()}</p>
            </div>
            
            <div className="event-description">
              <h4>Description</h4>
              <p>{selectedEvent.description}</p>
            </div>
            
            {selectedEvent.effects.length > 0 && (
              <div className="event-effects">
                <h4>Event Effects</h4>
                <ul>
                  {selectedEvent.effects.map((effect, index) => (
                    <li key={index}>
                      <strong>{effect.type}:</strong> {effect.target} 
                      ({effect.modifier}x for {effect.duration_hours}h)
                    </li>
                  ))}
                </ul>
              </div>
            )}
            
            {selectedEvent.affected_regions.length > 0 && (
              <div className="affected-regions">
                <h4>Affected Regions</h4>
                <ul>
                  {selectedEvent.affected_regions.map((region, index) => (
                    <li key={index}>{region}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {/* Pagination */}
        <div className="pagination">
          <button 
            onClick={() => setPage(page - 1)} 
            disabled={page === 1}
          >
            Previous
          </button>
          <span>Page {page} of {totalPages}</span>
          <button 
            onClick={() => setPage(page + 1)} 
            disabled={page === totalPages}
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
};

export default EventManagement;