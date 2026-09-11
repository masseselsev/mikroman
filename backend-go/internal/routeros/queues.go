package routeros

import (
	"context"
	"errors"
	"fmt"
)

// GetSimpleQueues reads /queue/simple
func (c *Client) GetSimpleQueues(ctx context.Context) ([]SimpleQueue, error) {
	var queues []SimpleQueue
	if err := c.Get(ctx, "/queue/simple", &queues); err != nil {
		return nil, err
	}
	return queues, nil
}

// CreateSimpleQueue creates a new simple queue via PUT /queue/simple
func (c *Client) CreateSimpleQueue(ctx context.Context, q *SimpleQueue) error {
	var res SimpleQueue
	if err := c.Put(ctx, "/queue/simple", q, &res); err != nil {
		return err
	}
	if res.ID != "" {
		q.ID = res.ID
	}
	return nil
}

// UpdateSimpleQueue updates fields of a queue via PATCH /queue/simple/{id}
func (c *Client) UpdateSimpleQueue(ctx context.Context, id string, fields map[string]interface{}) error {
	path := fmt.Sprintf("/queue/simple/%s", id)
	return c.Patch(ctx, path, fields, nil)
}

// DeleteSimpleQueue deletes a simple queue via DELETE /queue/simple/{id}
func (c *Client) DeleteSimpleQueue(ctx context.Context, id string) error {
	path := fmt.Sprintf("/queue/simple/%s", id)
	err := c.Delete(ctx, path)
	if errors.Is(err, ErrNotFound) {
		return nil
	}
	return err
}

// MoveSimpleQueue hoists a simple queue before another queue in RouterOS
func (c *Client) MoveSimpleQueue(ctx context.Context, id, beforeID string) error {
	path := fmt.Sprintf("/queue/simple/%s/move-before", id)
	payload := map[string]string{
		"destination": beforeID,
	}
	return c.Post(ctx, path, payload, nil)
}
