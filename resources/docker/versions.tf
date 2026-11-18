terraform {
  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      # Pinned: ppc64le/s390x have no registry builds, so albs-deploy
      # (roles/alts_terraform) compiles this exact version into the plugin
      # cache. Bump together with docker_build_ver in the inventories.
      version = "4.6.0"
    }
  }
  required_version = ">= 0.14"
}
