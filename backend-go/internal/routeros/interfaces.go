package routeros

import "context"

// GetInterfaces reads /interface
func (c *Client) GetInterfaces(ctx context.Context) ([]Interface, error) {
	var ifaces []Interface
	if err := c.Get(ctx, "/interface", &ifaces); err != nil {
		return nil, err
	}
	return ifaces, nil
}
