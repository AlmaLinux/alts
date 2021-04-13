terraform {
  required_providers {
    docker = {
      source = "kreuzwerker/docker"
    }
    google = {
      source = "hashicorp/google"
    }
    random = {
      source = "hashicorp/random"
    }
  }
  required_version = ">= 0.13"
}
