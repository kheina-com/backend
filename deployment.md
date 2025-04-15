configure docker to use the gcloud docker image repo
```sh
gcloud auth configure-docker <REGION>-docker.pkg.dev
docker push us-central1-docker.pkg.dev/kheinacom/fuzzly-repo/fuzzly-backend:$(git rev-parse --short HEAD)
```

connect to gke from kubectl
https://cloud.google.com/kubernetes-engine/docs/deploy-app-cluster#get_authentication_credentials_for_the_cluster
```sh
gcloud container clusters get-credentials fuzzly-backend \
	--location us-central1
```

create a new secret
https://kubernetes.io/docs/tasks/configmap-secret/managing-secret-using-kubectl/#create-a-secret
```sh
python3 init.py upload-secret -s credentials/creds.aes -n credentials
```

read a secret
```sh
python3 init.py kube-secret -s credentials
```

send deployment to gke
https://cloud.google.com/kubernetes-engine/docs/tutorials/hello-app#cloud-shell_2
```sh
kubectl apply -f k8s.yml
```

monitor deployment
```sh
kubectl get service
watch kubectl get pods
```

in order to update secrets, you must create or edit the existing credential file(s) and then re-encrypt them using `python3 init.py encrypt` then edit the kube secrets using
note: during edit, the contents of secrets must be `urlsafe_b64encode`d
```sh
kubectl edit secrets kh-aes
kubectl edit secrets kh-ed25519
kubectl edit secrets credentials
```

in order to update ssl certs, you must run certbot with cloudflare credentials, load the fullchain and privkey files into a json file and then update the `cert` kube secret
```sh
sudo certbot certonly
...
```
```python
(.venv) % python3
Python 3.12.4 (main, Jun  7 2024, 06:33:07) [GCC 14.1.1 20240522] on linux
Type "help", "copyright", "credits" or "license" for more information.
>>> import json
>>> fullchain = open('/etc/letsencrypt/live/fuzz.ly/fullchain.pem').read()
>>> privkey = open('/etc/letsencrypt/live/fuzz.ly/privkey.pem').read()
>>> json.dump({ 'fullchain': fullchain, 'privkey': privkey }, open('credentials/cert.json', 'w'))
```
```sh
kubectl delete secret cert
kubectl create secret generic cert \
	--from-file=cert.json=credentials/cert.json
```
