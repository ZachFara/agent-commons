# Two hosted environments (decision 0022). Dev is the local Docker setup.
locals {
  environments = {
    production = { service_name = "agent-commons", max_instances = 2 }
    staging    = { service_name = "agent-commons-staging", max_instances = 1 }
  }
}
